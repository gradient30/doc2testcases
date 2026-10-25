# -*- coding: utf-8 -*-
"""doc2testcases Web 外壳：上传 -> 任务编排（后台线程）-> 实时进度 -> 五格式下载。

运行: python web/app.py  ->  http://127.0.0.1:8787/
所有生成/评审/渲染逻辑都在 core/ 内核，本文件只做 HTTP 与任务生命周期。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(BASE_DIR))

from core.jobs import JobCanceled, process_job  # noqa: E402
from core.llm import LLMConfig  # noqa: E402
from core.parsers import SUPPORTED_EXTS  # noqa: E402
from core.renderer import FORMATS  # noqa: E402
from core.store import Store  # noqa: E402

DATA_DIR = BASE_DIR / "data"
JOBS_DIR = DATA_DIR / "jobs"
WEB_DIR = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 8787

store = Store(DATA_DIR / "doc2tc.db")
_RUNNING: dict[str, dict] = {}          # job_id -> {"cancel": Event}
_REGISTRY_LOCK = threading.Lock()
MEDIA_TYPES = {"xls": "application/vnd.ms-excel", "yaml": "text/yaml", "json": "application/json",
               "md": "text/markdown", "xmind": "application/zip"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    interrupted = store.recover_on_startup()
    if interrupted:
        print(f"[doc2tc] 恢复检查：{len(interrupted)} 个任务因进程重启标记为可重试续跑")
    yield
    store.close()


app = FastAPI(title="doc2testcases", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")


# ---------------------------------------------------------------- 公共工具

def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _llm_config() -> LLMConfig:
    s = store.settings_all()
    if not s.get("api_key"):
        raise HTTPException(400, "未配置 API Key：请先在「设置」中保存（或用环境变量 DOC2TC_API_KEY 启动）")
    try:
        return LLMConfig(api_key=s["api_key"], base_url=s["base_url"], model=s["model"],
                         style=s["style"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"模型配置无效: {e}") from e


def _render_payload(report) -> dict:
    return {"ok": report.ok, "lines": report.lines, "failures": report.failures,
            "documents": report.documents, "cases": report.cases,
            "files": sorted(report.files.keys())}


def _unique_upload_path(dir_: Path, original: str) -> Path:
    stem = _safe_stem(original)
    suffix = Path(original).suffix.lower()
    candidate = dir_ / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = dir_ / f"{stem}-{n}{suffix}"
        n += 1
    return candidate


def _safe_stem(name: str) -> str:
    import re
    stem = re.sub(r"\.[^.]+$", "", name)
    return re.sub(r"[\\/:*?\"<>|\r\n\t]+", "-", stem).strip("-") or "doc"


# ---------------------------------------------------------------- 任务执行

def _worker(job_id: str, *, resume: bool, fresh: bool) -> None:
    job = store.get_job(job_id)
    if job is None:
        return
    files = sorted(( _job_dir(job_id) / "uploads").glob("*"))
    rounds = int(job.get("params", {}).get("rounds", 2))
    cancel = threading.Event()
    with _REGISTRY_LOCK:
        _RUNNING[job_id] = {"cancel": cancel}
    try:
        store.update_job(job_id, status="running", error="")

        def on_update(docs, _stage):
            store.update_job(job_id, docs=[d.to_dict() for d in docs])

        if fresh:
            shutil.rmtree(_job_dir(job_id) / "testcases", ignore_errors=True)
        outcome = process_job(files, _llm_config(), rounds=rounds,
                              job_dir=_job_dir(job_id), on_update=on_update,
                              cancel_check=cancel.is_set, resume=resume)
        status = "done" if outcome.ok else "failed"
        store.update_job(job_id, status=status,
                         docs=[d.to_dict() for d in outcome.docs],
                         render=_render_payload(outcome.render), error=outcome.error)
    except JobCanceled:
        store.update_job(job_id, status="canceled", error="已取消（已完成文档可续跑复用）")
    except HTTPException as e:   # 设置缺 Key 等
        store.update_job(job_id, status="failed", error=str(e.detail))
    except Exception as e:  # noqa: BLE001
        store.update_job(job_id, status="failed", error=f"内部错误: {e}")
    finally:
        with _REGISTRY_LOCK:
            _RUNNING.pop(job_id, None)


def _start_worker(job_id: str, *, resume: bool = True, fresh: bool = False) -> None:
    t = threading.Thread(target=_worker, args=(job_id,), kwargs={"resume": resume, "fresh": fresh},
                         daemon=True, name=f"job-{job_id}")
    t.start()


def _is_running(job_id: str) -> bool:
    with _REGISTRY_LOCK:
        return job_id in _RUNNING


# ---------------------------------------------------------------- API

@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "static" / "index.html")


@app.get("/api/health")
def health() -> dict:
    s = store.settings_all()
    return {"ok": True, "version": "1.0.0", "key_configured": bool(s.get("api_key")),
            "model": s.get("model")}


@app.get("/api/settings")
def get_settings() -> dict:
    s = store.settings_all()
    key = s.get("api_key", "")
    s["api_key_masked"] = (key[:4] + "…" + key[-4:]) if len(key) > 12 else ("已配置" if key else "")
    s["api_key"] = ""
    return s


@app.post("/api/settings")
async def save_settings(payload: dict) -> dict:
    partial = {k: payload[k] for k in ("base_url", "model", "style", "rounds")
               if k in payload and str(payload[k]).strip() != ""}
    if payload.get("api_key") and str(payload["api_key"]).strip() not in ("", "（未更改）"):
        partial["api_key"] = str(payload["api_key"]).strip()
    if partial.get("style") not in (None, "openai", "anthropic"):
        raise HTTPException(400, "style 只能是 openai 或 anthropic")
    return _settings_view(store.settings_save(partial))


def _settings_view(s: dict) -> dict:
    key = s.get("api_key", "")
    s["api_key_masked"] = (key[:4] + "…" + key[-4:]) if len(key) > 12 else ("已配置" if key else "")
    s["api_key"] = ""
    return s


@app.post("/api/jobs")
async def create_job(files: list[UploadFile] = File(...), rounds: int = Form(default=0)) -> dict:
    if not files:
        raise HTTPException(400, "请至少上传一份文档")
    bad = [f.filename for f in files
           if not f.filename or Path(f.filename).suffix.lower() not in SUPPORTED_EXTS]
    if bad:
        raise HTTPException(400, f"不支持的格式: {', '.join(bad)}（支持 md/pdf/docx/xlsx/xls）")
    _llm_config()  # 提前校验 Key，避免任务建好才失败
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    up_dir = _job_dir(job_id) / "uploads"
    up_dir.mkdir(parents=True, exist_ok=True)
    names = []
    for f in files:
        target = _unique_upload_path(up_dir, f.filename or "doc")
        target.write_bytes(await f.read())
        names.append(target.name)
    if rounds <= 0:
        rounds = int(store.settings_all().get("rounds", 2))
    store.create_job(job_id, params={"rounds": rounds, "files": names})
    _start_worker(job_id)
    return {"id": job_id}


@app.get("/api/jobs")
def list_jobs() -> dict:
    jobs = store.list_jobs()
    for j in jobs:
        j["running"] = _is_running(j["id"])
    return {"jobs": jobs}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    job["running"] = _is_running(job_id)
    return job


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str):
    """SSE：任务版本变化时推送快照，终态推送后关闭。"""
    if store.get_job(job_id) is None:
        raise HTTPException(404, "任务不存在")

    async def gen():
        last_version = -1
        idle = 0.0
        while True:
            job = store.get_job(job_id)
            if job is None:
                break
            if job["version"] != last_version:
                last_version = job["version"]
                idle = 0.0
                yield f"data: {json.dumps(job, ensure_ascii=False)}\n\n"
                if job["status"] in ("done", "failed", "canceled", "interrupted"):
                    break
            else:
                idle += 1.0
                if idle >= 15:  # 心跳，避免代理断连
                    idle = 0.0
                    yield ": ping\n\n"
            await asyncio.sleep(1.0)

    return _sse(gen())


def _sse(gen):
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    if store.get_job(job_id) is None:
        raise HTTPException(404, "任务不存在")
    with _REGISTRY_LOCK:
        entry = _RUNNING.get(job_id)
    if entry is None:
        raise HTTPException(400, "任务不在运行中")
    entry["cancel"].set()
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, fresh: bool = False) -> dict:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    if _is_running(job_id):
        raise HTTPException(400, "任务正在运行")
    if job["status"] not in ("failed", "canceled", "interrupted", "done"):
        raise HTTPException(400, f"状态 {job['status']} 无需重试")
    _llm_config()
    _start_worker(job_id, resume=not fresh, fresh=fresh)
    return {"ok": True}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    if _is_running(job_id):
        raise HTTPException(400, "任务运行中，请先取消")
    if store.get_job(job_id) is None:
        raise HTTPException(404, "任务不存在")
    store.delete_job(job_id)
    shutil.rmtree(_job_dir(job_id), ignore_errors=True)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/download/{fmt}")
def download(job_id: str, fmt: str) -> FileResponse:
    if fmt not in FORMATS:
        raise HTTPException(404, f"未知格式 {fmt}")
    path = _job_dir(job_id) / "out" / f"TestCases.{fmt}"
    if not path.is_file():
        raise HTTPException(404, "产物尚未生成或校验未通过")
    return FileResponse(path, media_type=MEDIA_TYPES.get(fmt, "application/octet-stream"),
                        filename=f"TestCases.{fmt}")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    print(f"doc2testcases 控制台: http://{HOST}:{PORT}/  （Ctrl+C 退出）")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")

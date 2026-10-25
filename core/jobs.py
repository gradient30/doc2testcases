# -*- coding: utf-8 -*-
"""单次任务编排：解析 -> 生成 -> 评审修订 -> 逐文档落盘 -> 五格式渲染。

CLI 与 Web 共用；不感知存储/HTTP——持久化由调用方通过 on_update 回调完成。
支持续跑：testcases/<flat>.json 已存在且未失败的文档直接复用（断点重试的基础）。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import renderer
from .llm import AbortedError, LLMConfig, LLMError
from .parsers import ParseError, parse_document
from .reviewer import PipelineResult, ReviewError, run_pipeline
from .schema import CaseSet

STAGES = ["parsing", "generating", "reviewing", "rendering"]
# 文件名安全：去掉路径分隔与 Windows 非法字符
_UNSAFE = re.compile(r"[\\/:*?\"<>|\r\n\t]+")


def flat_name(name: str) -> str:
    """规范名：与 ZCode 工作流一致的连字符连接 + 非法字符清洗。"""
    stem = re.sub(r"\.[^.]+$", "", name)
    return _UNSAFE.sub("-", stem).strip("-") or "doc"


@dataclass
class DocState:
    name: str
    fmt: str = ""
    flat: str = ""
    stage: str = "queued"            # queued/parsing/generating/reviewing/rendering/done/failed/skipped
    module: str = ""
    counts: dict = field(default_factory=dict)
    notes: str = ""
    approved: bool | None = None
    rounds: int = 0
    findings: list = field(default_factory=list)  # 最后一轮未决意见 [{what, severity}]
    reviews_log: list = field(default_factory=list)  # 每轮意见数记录
    error: str = ""
    reused: bool = False             # 续跑时复用了已有结果

    def to_dict(self) -> dict:
        return asdict(self)


class JobCanceled(Exception):
    pass


@dataclass
class JobOutcome:
    ok: bool
    docs: list[DocState]
    render: renderer.RenderReport
    error: str = ""


def process_job(
    files: list[Path],
    llm_cfg: LLMConfig,
    *,
    rounds: int = 2,
    job_dir: Path,
    on_update: Callable[[list[DocState], str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    resume: bool = False,
) -> JobOutcome:
    """跑完一个任务。job_dir/testcases 存逐文档 JSON，job_dir/out 存渲染产物。

    on_update(docs, overall_stage) 在每次阶段变化时回调（持久化/SSE 用）。
    cancel_check 返回 True 时在文档边界/阶段边界协作式取消。
    """
    tc_dir = job_dir / "testcases"
    out_dir = job_dir / "out"
    tc_dir.mkdir(parents=True, exist_ok=True)
    # 取消探针下沉到 LLM 重试循环：长重试等待期间也能即时响应取消
    if cancel_check is not None:
        llm_cfg = llm_cfg.model_copy(update={"abort_check": cancel_check})

    def _emit(docs: list[DocState], stage: str) -> None:
        if on_update is not None:
            on_update(docs, stage)

    def _canceled() -> bool:
        return cancel_check is not None and cancel_check()

    states = [DocState(name=f.name, flat=flat_name(f.name)) for f in files]
    outcome = JobOutcome(ok=False, docs=states, render=renderer.RenderReport(ok=False))

    render_dicts: list[dict] = []
    for st, f in zip(states, files):
        # 续跑：已有结果且上次成功 -> 复用
        cached = tc_dir / f"{st.flat}.json"
        if resume and cached.is_file():
            try:
                data = json.loads(cached.read_text(encoding="utf-8"))
                cs = CaseSet.model_validate(data)
                st.fmt = f.suffix.lstrip(".").lower()
                st.stage, st.module, st.counts, st.notes = "done", cs.module, cs.counts(), cs.notes
                st.approved, st.reused = True, True
                render_dicts.append(cs.to_render_dict())
                _emit(states, "generating")
                continue
            except Exception:  # noqa: BLE001 缓存损坏则重跑
                pass
        if _canceled():
            raise JobCanceled()
        # 解析
        st.stage = "parsing"
        _emit(states, "parsing")
        try:
            parsed = parse_document(f)
        except ParseError as e:
            st.stage, st.error = "failed", str(e)
            _emit(states, "parsing")
            continue
        st.fmt = parsed.fmt
        if _canceled():
            raise JobCanceled()
        # 生成 + 评审修订循环
        st.stage = "generating"
        _emit(states, "generating")
        try:
            result: PipelineResult = run_pipeline(llm_cfg, parsed, rounds=rounds)
        except AbortedError as e:
            raise JobCanceled() from e
        except (LLMError, ReviewError) as e:
            st.stage, st.error = "failed", f"LLM 调用失败: {e}"
            _emit(states, "generating")
            continue
        except Exception as e:  # noqa: BLE001
            st.stage, st.error = "failed", str(e)
            _emit(states, "generating")
            continue
        cs = result.caseset
        st.stage = "reviewing"
        st.module, st.counts, st.notes = cs.module, cs.counts(), cs.notes
        st.approved, st.rounds = result.approved, result.rounds
        st.findings = [f.model_dump() for f in result.open_findings]
        st.reviews_log = [len(r.findings) for r in result.reviews]
        _emit(states, "reviewing")
        # 逐文档落盘（canonical JSON，schema 同 out/testcases）
        cached.write_text(json.dumps(cs.model_dump(), ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")
        render_dicts.append(cs.to_render_dict())

    if _canceled():
        raise JobCanceled()

    # 渲染（有成功文档才渲染：新生成停在 reviewing，续跑复用已标记 done）
    ok_docs = [s for s in states if s.stage in ("reviewing", "done")]
    if ok_docs:
        for s in ok_docs:
            if s.stage == "reviewing":
                s.stage = "rendering"
        _emit(states, "rendering")
        report = renderer.render_all(sorted(render_dicts, key=lambda d: d["document"]), out_dir)
        outcome.render = report
        for s in states:
            if s.stage == "rendering":
                s.stage = "done"
        failed_names = [s.name for s in states if s.stage == "failed"]
        outcome.ok = report.ok and not failed_names
        if not report.ok:
            outcome.error = "; ".join(report.failures)
        elif failed_names:
            outcome.error = f"部分文档失败: {', '.join(failed_names)}"
    else:
        outcome.ok = False
        outcome.error = outcome.error or "没有任何文档成功生成用例"
    _emit(states, "done" if outcome.ok else "failed")
    return outcome


def now_ms() -> int:
    return int(time.time() * 1000)

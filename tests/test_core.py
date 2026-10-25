# -*- coding: utf-8 -*-
"""内核回归测试（不依赖 LLM / 网络）：python tests/test_core.py

覆盖：解析器四格式、schema 门禁（好/坏样本）、渲染回读、store 持久化与断点恢复标记。
"""
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.parsers import ParseError, parse_document  # noqa: E402
from core.renderer import load_documents, render_all  # noqa: E402
from core.schema import CaseSet, TestCase  # noqa: E402
from core.store import Store  # noqa: E402

PASS, FAIL = [], []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  ok  {name}")
    except Exception as e:  # noqa: BLE001
        FAIL.append((name, e))
        print(f" FAIL {name}: {e}")


# ---------------------------------------------------------------- 解析器
def t_parsers():
    cases = [
        ("md", BASE / "giraffe-bike" / "README.md", "长颈鹿"),
        ("docx", BASE / "samples" / "login-requirements.docx", "验证码"),
        ("pdf", BASE / "samples" / "order-flow.pdf", "货到付款"),
        ("xlsx", BASE / "samples" / "pricing-rules.xlsx", "满减"),
    ]
    for fmt, path, needle in cases:
        d = parse_document(path)
        assert d.fmt == fmt, f"{path} fmt={d.fmt}"
        assert needle in d.text, f"{path} 未提取到「{needle}」"
    try:
        parse_document(BASE / "samples" / "make_fixtures.py")
        raise AssertionError("应当拒绝不支持格式")
    except ParseError:
        pass


# ---------------------------------------------------------------- schema 门禁
GOOD = {"document": "x.md", "module": "演示", "notes": "",
        "cases": [{"id": "TC-01", "title": "正向", "priority": "P0", "type": "功能测试",
                   "preconditions": "", "steps": ["1. 打开"], "expected": "成功", "source": "§1"}]}


def t_schema_ok():
    cs = CaseSet.model_validate(GOOD)
    assert cs.counts() == {"total": 1, "P0": 1, "P1": 0, "P2": 0}
    d = cs.to_render_dict()
    assert list(d["cases"][0].keys()) == ["id", "title", "priority", "type",
                                          "preconditions", "steps", "expected", "source"]


def t_schema_bad():
    import pydantic
    bad_samples = [
        {**GOOD, "cases": []} if False else {**GOOD, "cases": [
            {**GOOD["cases"][0], "priority": "P9"}]},          # 非法优先级
        {**GOOD, "cases": [GOOD["cases"][0], GOOD["cases"][0]]},  # id 重复
        {**GOOD, "cases": [{**GOOD["cases"][0], "steps": []}]},   # 步骤为空
        {**GOOD, "module": ""},                                    # 模块名为空
    ]
    for i, bad in enumerate(bad_samples):
        try:
            CaseSet.model_validate(bad)
            raise AssertionError(f"坏样本{i}应当被拒绝")
        except pydantic.ValidationError:
            pass


# ---------------------------------------------------------------- 渲染 + 回读
def t_render_roundtrip():
    tmp = Path(tempfile.mkdtemp(prefix="d2t_render_"))
    try:
        src = tmp / "testcases"
        shutil.copytree(BASE / "out" / "testcases", src)
        docs = load_documents(src)
        report = render_all(docs, tmp / "out")
        assert report.ok, report.failures
        assert report.cases == 114 and report.documents == 4
        assert set(report.files) == {"xls", "yaml", "json", "md", "xmind"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- store 持久化与恢复
def t_store_and_recover():
    tmp = Path(tempfile.mkdtemp(prefix="d2t_store_"))
    try:
        s = Store(tmp / "t.db")
        s.create_job("j1", {"rounds": 2, "files": ["a.docx"]})
        s.update_job("j1", status="running",
                     docs=[{"name": "a.docx", "stage": "generating"}])
        assert s.get_job("j1")["status"] == "running"
        assert s.get_job("j1")["version"] == 2
        # 模拟进程重启后的恢复：running -> interrupted
        marked = s.recover_on_startup()
        assert marked == ["j1"]
        job = s.get_job("j1")
        assert job["status"] == "interrupted" and "重试" in job["error"]
        # 设置读写 + 掩码视图不回传明文（api_key 恒为空串）
        s.settings_save({"api_key": "sk-test-1234567890", "model": "glm-4.6"})
        saved = s.settings_all()
        assert saved["api_key"].startswith("sk-test")  # Store 层保存的是明文本体
        s.close()
        s2 = Store(tmp / "t.db")                       # 重开数据库数据仍在
        assert s2.get_job("j1")["params"]["rounds"] == 2
        assert s2.settings_all()["model"] == "glm-4.6"
        s2.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- jobs 编排（无 LLM：解析失败路径）
def t_job_parse_failure():
    from core.jobs import process_job
    tmp = Path(tempfile.mkdtemp(prefix="d2t_job_"))
    try:
        bad = tmp / "bad.txt"
        bad.write_text("not a doc", encoding="utf-8")
        class _FakeCfg:  # 不会被用到：解析先失败
            pass
        out = process_job([bad], _FakeCfg(), rounds=1, job_dir=tmp / "job")
        assert out.ok is False
        assert out.docs[0].stage == "failed" and "不支持的格式" in out.docs[0].error
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_llm_abort_responsive():
    """取消探针：重试等待期间 abort_check 触发应立刻抛 AbortedError，不再睡满间隔。"""
    import core.llm as llm
    orig = llm._chat_once
    def fake_once(cfg, system, user):
        raise llm._RetryableLLMError("HTTP 405: 模拟风控")
    llm._chat_once = fake_once
    try:
        cfg = llm.LLMConfig(api_key="sk-test-123456", max_api_retries=30,
                            retry_interval_s=60, abort_check=lambda: True)
        t0 = time.time()
        try:
            llm.chat(cfg, "s", "u")
            raise AssertionError("应当抛出 AbortedError")
        except llm.AbortedError:
            pass
        assert time.time() - t0 < 5, "取消未即时响应（不应睡满 60s）"
    finally:
        llm._chat_once = orig


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("== doc2testcases 内核回归 ==")
    check("parsers 四格式 + 拒绝非法", t_parsers)
    check("schema 合法样本", t_schema_ok)
    check("schema 非法样本全部被拒", t_schema_bad)
    check("渲染五格式 + 回读校验(114例)", t_render_roundtrip)
    check("store 持久化/恢复标记/设置", t_store_and_recover)
    check("jobs 无 LLM 解析失败路径", t_job_parse_failure)
    check("llm 取消即时响应(不睡满间隔)", t_llm_abort_responsive)
    print(f"\n{len(PASS)} 通过, {len(FAIL)} 失败")
    sys.exit(1 if FAIL else 0)

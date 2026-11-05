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


def t_llm_business_envelope():
    """HTTP 200 包着业务错误封套 {code,msg}：1005/3009 可重试，其他业务码显式报错，不得当成空成功。"""
    import core.llm as llm
    orig = llm._http_transport
    def fake_transport(status_payload):
        def _t(url, headers, payload, timeout):
            return status_payload
        return _t
    try:
        for code, retryable in ((1005, True), (3009, True), (429, True), (3006, False)):
            llm._http_transport = fake_transport((200, json.dumps({"code": code, "msg": "x"})))
            try:
                llm._post("http://x", {}, {}, 5)
                raise AssertionError("应当抛错")
            except llm._RetryableLLMError:
                assert retryable, f"code={code} 不应可重试"
            except llm.LLMError:
                assert not retryable, f"code={code} 应当可重试"
        # 正常封套不受影响
        llm._http_transport = fake_transport((200, json.dumps(
            {"choices": [{"message": {"content": "hi"}}]})))
        assert llm._post("http://x", {}, {}, 5)["choices"][0]["message"]["content"] == "hi"
    finally:
        llm._http_transport = orig


def t_extract_json():
    """模型回复 JSON 提取：围栏/裸 JSON/前后噪声/嵌套字符串中的花括号。"""
    from core.llm import LLMError, extract_json
    obj = {"a": 1, "b": "}虚假括号"}
    cases = [
        "```json\n" + json.dumps(obj, ensure_ascii=False) + "\n```",
        "前置说明……\n" + json.dumps(obj, ensure_ascii=False) + "\n后置说明",
        json.dumps([1, 2]),
    ]
    for c in cases:
        assert extract_json(c) in (obj, [1, 2])
    for bad in ("", "没有 JSON 的回复", '{"unterminated": true'):
        try:
            extract_json(bad)
            raise AssertionError("应当解析失败")
        except LLMError:
            pass


def t_providers_registry():
    """厂商预设注册表完整性：字段齐、default 在清单内、模型 ID 非空去重。"""
    from core.providers import PROVIDERS
    assert set(PROVIDERS) == {"glm", "deepseek", "mimo", "minimax", "custom"}
    for pid, p in PROVIDERS.items():
        assert p["label"] and p["style"] in ("openai", "anthropic")
        assert isinstance(p["models"], list) and len(p["models"]) == len(set(p["models"]))
        for m in p["models"]:
            assert m.strip() == m and m, f"{pid} 模型 ID 异常: {m!r}"
        if pid != "custom":
            assert p["base_url"].startswith("https://")
            assert p["default_model"] in p["models"]
            assert p["key_url"].startswith("https://")


def t_providers_migrate():
    """老平铺四字段 -> 新键集合：官方端点映射到对应厂商槽，未知端点归 custom。"""
    from core.providers import migrate_legacy_settings as mig
    # bigmodel 官方端点 -> glm 槽
    out = mig({"api_key": "sk-old-1234567890", "base_url": "https://open.bigmodel.cn/api/paas/v4",
               "model": "glm-4.6", "style": "openai"})
    assert out == {"provider": "glm", "api_key:glm": "sk-old-1234567890", "model:glm": "glm-4.6"}
    # 未知端点 -> custom，原值全保留
    out2 = mig({"api_key": "sk-x-1234567890", "base_url": "https://gw.example/v1",
                "model": "m1", "style": "anthropic"})
    assert out2 == {"provider": "custom", "api_key:custom": "sk-x-1234567890",
                    "base_url:custom": "https://gw.example/v1",
                    "model:custom": "m1", "style:custom": "anthropic"}
    # 尾斜杠也能匹配；空/新形状不迁移
    assert mig({"base_url": "https://api.deepseek.com/"}) == {"provider": "deepseek"}
    assert mig({}) == {} and mig({"provider": "glm"}) == {}


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
    check("llm 业务错误封套处理(1005/3009 可重试)", t_llm_business_envelope)
    check("llm 模型回复 JSON 提取", t_extract_json)
    check("providers 注册表完整性", t_providers_registry)
    check("providers 老配置迁移(纯函数)", t_providers_migrate)
    print(f"\n{len(PASS)} 通过, {len(FAIL)} 失败")
    sys.exit(1 if FAIL else 0)

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
        # 设置读写：新键集（厂商分槽）
        s.settings_save({"api_key:glm": "sk-test-1234567890", "model:glm": "glm-5.3",
                         "models:custom": "m1, m2"})
        saved = s.settings_all()
        assert saved["api_key:glm"].startswith("sk-test")  # Store 层保存的是明文本体
        assert saved["models:custom"] == "m1, m2"          # custom 常用模型清单（逗号分隔）
        s.close()
        s2 = Store(tmp / "t.db")                       # 重开数据库数据仍在
        assert s2.get_job("j1")["params"]["rounds"] == 2
        assert s2.settings_all()["model:glm"] == "glm-5.3"
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
    # 老库只存过 Key（无 base_url 行）-> 按老版默认端点（BigModel）归 glm
    assert mig({"api_key": "sk-only-1234567890"}) == {"provider": "glm", "api_key:glm": "sk-only-1234567890"}
    # 幂等：已存在新键时即使残留旧键也不再迁移（防迁移半途崩溃后旧值覆盖新值）
    assert mig({"provider": "glm", "api_key": "sk-old-1234567890"}) == {}


def t_resolve_llm_config():
    """配置组装：env > settings 槽 > 预设默认；缺 Key/缺 custom 配置/未知厂商显式报错。"""
    from core.llm import LLMConfig
    from core.providers import ProviderError, resolve_llm_config
    cfg = resolve_llm_config({"provider": "deepseek", "api_key:deepseek": "sk-ds-1234567890",
                              "model:deepseek": "deepseek-v4-pro"}, env={})
    assert isinstance(cfg, LLMConfig)
    assert (cfg.base_url, cfg.model, cfg.style) == ("https://api.deepseek.com", "deepseek-v4-pro", "openai")
    # 预设默认模型 + env Key 覆盖
    cfg2 = resolve_llm_config({"provider": "glm"}, env={"DOC2TC_API_KEY": "sk-env-1234567890"})
    assert cfg2.model == "glm-5.3" and cfg2.api_key == "sk-env-1234567890"
    # env 的 provider/model 覆盖 settings
    cfg3 = resolve_llm_config({"provider": "glm"},
                              env={"DOC2TC_PROVIDER": "minimax", "DOC2TC_API_KEY": "sk-mm-1234567890",
                                   "DOC2TC_MODEL": "MiniMax-M3"})
    assert cfg3.base_url == "https://api.minimaxi.com/v1" and cfg3.model == "MiniMax-M3"
    # custom 走专属槽
    cfg4 = resolve_llm_config({"provider": "custom", "api_key:custom": "sk-c-1234567890",
                               "base_url:custom": "https://gw/v1", "style:custom": "anthropic",
                               "model:custom": "m1"}, env={})
    assert (cfg4.base_url, cfg4.style, cfg4.model) == ("https://gw/v1", "anthropic", "m1")
    # 探测参数覆盖构造（与 web /api/test 同款表达式）：完整走校验且不与 dump 键重复传参
    probe = LLMConfig(**{**cfg4.model_dump(), "max_tokens": 1024,
                         "timeout_s": 30, "max_api_retries": 0})
    assert probe.max_tokens == 1024 and probe.timeout_s == 30 and probe.max_api_retries == 0
    for bad_settings, needle in (
            ({"provider": "deepseek"}, "DeepSeek"),                    # 缺 Key 带厂商名
            ({"provider": "custom", "api_key:custom": "sk-c-1234567890"}, "Base URL"),  # custom 缺 url
            ({"provider": "nope"}, "未知厂商")):                       # 未知厂商
        try:
            resolve_llm_config(bad_settings, env={})
            raise AssertionError("应当抛 ProviderError")
        except ProviderError as e:
            assert needle in str(e), str(e)


def t_settings_migration():
    """settings_all 读取时自动迁移老键并落库删除；未知端点归 custom 且原值保留。"""
    from core.store import Store
    tmp = Path(tempfile.mkdtemp(prefix="d2t_mig_"))
    try:
        s = Store(tmp / "a.db")
        with s._lock, s._conn:  # 直接写老键模拟旧版数据库（绕过新白名单）
            s._conn.executemany(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                [("api_key", "sk-old-1234567890"),
                 ("base_url", "https://open.bigmodel.cn/api/paas/v4"),
                 ("model", "glm-4.6"), ("style", "openai")])
        got = s.settings_all()
        assert got["provider"] == "glm" and got["api_key:glm"].startswith("sk-old")
        assert got["model:glm"] == "glm-4.6"
        with s._lock:  # 旧键已从库中删除
            left = {r[0] for r in s._conn.execute("SELECT key FROM settings")}
        assert not (left & {"api_key", "base_url", "model", "style"})
        s.close()
        s2 = Store(tmp / "b.db")  # 未知端点 -> custom，原值保留
        with s2._lock, s2._conn:
            s2._conn.executemany(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                [("api_key", "sk-x-1234567890"), ("base_url", "https://gw.example/v1")])
        got2 = s2.settings_all()
        assert got2["provider"] == "custom" and got2["api_key:custom"] == "sk-x-1234567890"
        assert got2["base_url:custom"] == "https://gw.example/v1"
        s2.close()
        s3 = Store(tmp / "c.db")  # 升级后首个请求就是保存：不被迁移旧值覆盖
        with s3._lock, s3._conn:
            s3._conn.execute("INSERT INTO settings (key, value) VALUES ('api_key', 'sk-old-1234567890')")
        s3.settings_save({"api_key:deepseek": "sk-new-1234567890"})
        got3 = s3.settings_all()
        assert got3["api_key:deepseek"] == "sk-new-1234567890"
        assert got3["api_key:glm"] == "sk-old-1234567890"  # 旧 Key 无端点行 -> 归 glm（老默认端点）
        s3.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_logbus():
    """日志总线：seq 递增、增量拉取、maxlen 淘汰最旧、并发 append 不丢不乱。"""
    import threading as _th
    from core import logbus
    logbus.reset()
    logbus.log("info", "t", "a")
    logbus.log("warn", "t", "b")
    logbus.log("error", "t", "c")
    all3 = logbus.tail()
    assert [e["level"] for e in all3] == ["info", "warn", "error"]
    assert all3[0]["seq"] < all3[1]["seq"] < all3[2]["seq"]
    assert logbus.tail(after_seq=all3[0]["seq"]) == all3[1:]      # 增量拉取
    assert logbus.tail(after_seq=all3[-1]["seq"]) == []           # 无新增
    logbus.log("bogus", "t", "x")                                  # 非法级别归 info
    assert logbus.tail()[-1]["level"] == "info"
    for i in range(logbus._MAX_ENTRIES + 50):                      # 环形淘汰最旧
        logbus.log("info", "t", f"m{i}")
    kept = logbus.tail(limit=10 ** 9)
    assert len(kept) == logbus._MAX_ENTRIES and kept[0]["msg"] == "m50"

    def _hammer():
        for _ in range(200):
            logbus.log("info", "t", "h")

    logbus.reset()                                                 # 并发 append 不丢不重
    ts = [_th.Thread(target=_hammer) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    got = logbus.tail(limit=10 ** 9)
    assert len(got) == 800
    seqs = [e["seq"] for e in got]
    assert seqs == sorted(seqs) and len(set(seqs)) == 800
    logbus.reset()


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
    check("providers 配置组装 resolve_llm_config", t_resolve_llm_config)
    check("settings 老库迁移落库", t_settings_migration)
    check("logbus 环形缓冲/增量/并发", t_logbus)
    print(f"\n{len(PASS)} 通过, {len(FAIL)} 失败")
    sys.exit(1 if FAIL else 0)

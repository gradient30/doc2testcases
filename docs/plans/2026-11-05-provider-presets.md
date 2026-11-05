# 厂商预设（GLM / DeepSeek / MiMo / MiniMax）实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 设置内置四家官方厂商预设（最新模型清单、按厂商分槽存 Key、只填 Key 即用），自定义 OpenAI/Anthropic 兼容端点降为进阶选项，Web 与 CLI 同步支持。

**Architecture:** 新增 `core/providers.py`（纯数据注册表 + `resolve_llm_config` 组装 + 老配置迁移），`store.py` settings 换新键集合（`provider` / `model:<p>` / `api_key:<p>` / `base_url:custom` / `style:custom`），Web 与 CLI 壳都改调内核的 `resolve_llm_config`。`core/llm.py` 传输层零改动（四家全为 OpenAI 兼容）。

**Tech Stack:** Python 3.10+ / pydantic v2 / FastAPI / 原生 HTML+JS 单页 / SQLite。测试为手写 runner `python tests/test_core.py`（非 pytest）。

**设计文档:** `docs/plans/2026-11-05-provider-presets-design.md`（已批准）。

**约定:** 全程仓库根目录执行命令；提交信息中文 conventional 前缀；注释中文。唯一模型 ID 勘误：`glm-4.7-flash` 不带「（免费）」后缀（后缀会污染提交给 API 的 model 参数）。

---

### Task 1: core/providers.py 注册表 + 完整性测试

**Files:**
- Create: `core/providers.py`
- Test: `tests/test_core.py`

**Step 1: 写失败测试**（`tests/test_core.py` 顶部 import 区不动，在 `t_extract_json` 之后加函数，`__main__` 注册行同步加）:

```python
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
```

`__main__` 块追加：`check("providers 注册表完整性", t_providers_registry)`

**Step 2: 跑测试确认失败**

Run: `python tests/test_core.py`
Expected: 新增项 FAIL（`ModuleNotFoundError: No module named 'core.providers'`），原 9 项 ok。

**Step 3: 写实现**（新文件 `core/providers.py`，注册表部分）:

```python
# -*- coding: utf-8 -*-
"""厂商预设注册表：GLM / DeepSeek / MiMo / MiniMax 官方端点 + 最新模型清单，custom 为自定义兼容端点。

规格唯一：CLI 与 Web 都经 resolve_llm_config 组装 LLMConfig，预设只写这一份。
端点与模型清单核实于 2026-11（各官方文档）；厂商上新模型可直接手填模型 ID，无需等本表更新。
"""
from __future__ import annotations

import os
from typing import Mapping

from .llm import LLMConfig

PROVIDERS: dict[str, dict] = {
    "glm": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "style": "openai",
        "default_model": "glm-5.3",
        "models": ["glm-5.3", "glm-5.3-flash", "glm-5.2", "glm-5.1",
                    "glm-5-turbo", "glm-4.7", "glm-4.7-flash", "glm-4.6"],
        "key_url": "https://open.bigmodel.cn/usercenter/apikeys",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "style": "openai",
        "default_model": "deepseek-flash",
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "key_url": "https://platform.deepseek.com/api_keys",
    },
    "mimo": {
        "label": "小米 MiMo",
        "base_url": "https://api.xiaomimimo.com/v1",
        "style": "openai",
        "default_model": "mimo-v2.6-pro",
        "models": ["mimo-v2.6-pro", "mimo-v2.6-flash"],
        "key_url": "https://platform.xiaomimimo.com",
    },
    "minimax": {
        "label": "MiniMax",
        "base_url": "https://api.minimaxi.com/v1",
        "style": "openai",
        "default_model": "MiniMax-M3.1-Flash",
        "models": ["MiniMax-M3.1-Flash", "MiniMax-M3"],
        "key_url": "https://platform.minimaxi.com",
    },
    "custom": {
        "label": "自定义（OpenAI / Anthropic 兼容）",
        "base_url": "", "style": "openai", "default_model": "",
        "models": [], "key_url": "",
    },
}

_BY_BASE_URL = {v["base_url"].rstrip("/"): k for k, v in PROVIDERS.items() if k != "custom"}
_LEGACY_KEYS = ("api_key", "base_url", "model", "style")


class ProviderError(Exception):
    """厂商预设/配置错误（未知厂商、缺 Key、custom 配置不全）。"""
```

**Step 4: 跑测试确认通过**

Run: `python tests/test_core.py`
Expected: `10 通过, 0 失败`。

**Step 5: Commit**

```bash
git add core/providers.py tests/test_core.py
git commit -m "feat: 厂商预设注册表（GLM/DeepSeek/MiMo/MiniMax + custom，2026-11 官方端点与模型清单）"
```

---

### Task 2: migrate_legacy_settings 老配置迁移（纯函数）

**Files:**
- Modify: `core/providers.py`（追加函数）
- Test: `tests/test_core.py`

**Step 1: 写失败测试**（新增 `t_providers_migrate`，`__main__` 注册）:

```python
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
```

**Step 2: 跑测试确认失败**（`ImportError: cannot import name 'migrate_legacy_settings'`）

**Step 3: 实现**（`core/providers.py` 追加）:

```python
def migrate_legacy_settings(raw: dict) -> dict:
    """旧版平铺四字段（api_key/base_url/model/style）-> 新键集合。非旧形状返回 {}。"""
    if not any(raw.get(k) for k in _LEGACY_KEYS):
        return {}
    provider = _BY_BASE_URL.get((raw.get("base_url") or "").rstrip("/"), "custom")
    out: dict = {"provider": provider}
    if provider == "custom":
        for k, nk in (("api_key", "api_key:custom"), ("base_url", "base_url:custom"),
                      ("model", "model:custom"), ("style", "style:custom")):
            if raw.get(k):
                out[nk] = raw[k]
    else:
        if raw.get("api_key"):
            out[f"api_key:{provider}"] = raw["api_key"]
        if raw.get("model"):
            out[f"model:{provider}"] = raw["model"]
    return out
```

**Step 4: 跑测试**（`11 通过, 0 失败`）

**Step 5: Commit** `git commit -m "feat: 老版平铺配置一次性迁移到厂商槽（官方端点反查厂商，未知归 custom）"`

---

### Task 3: resolve_llm_config 统一组装

**Files:**
- Modify: `core/providers.py`（追加函数）
- Test: `tests/test_core.py`

**Step 1: 写失败测试**（新增 `t_resolve_llm_config`，`__main__` 注册）:

```python
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
    for bad_settings, bad_env, needle in (
            ({"provider": "deepseek"}, {}, "DeepSeek"),                     # 缺 Key 带厂商名
            ({"provider": "custom", "api_key:custom": "sk-c-1234567890"}, {}, "Base URL"),  # custom 缺 url
            ({"provider": "nope"}, {}, "未知厂商")):                        # 未知厂商
        try:
            resolve_llm_config(bad_settings, env=bad_env)
            raise AssertionError("应当抛 ProviderError")
        except ProviderError as e:
            assert needle in str(e), str(e)
```

**Step 2: 跑测试确认失败**（`ImportError`）

**Step 3: 实现**（`core/providers.py` 追加）:

```python
def resolve_llm_config(settings: dict, env: Mapping[str, str] | None = None) -> LLMConfig:
    """settings（store.settings_all 形状）+ 环境变量 -> LLMConfig。优先级：env > settings 槽 > 预设默认。"""
    env = os.environ if env is None else env

    def _e(name: str) -> str:
        return (env.get(name) or "").strip()

    provider = (_e("DOC2TC_PROVIDER") or settings.get("provider") or "glm").strip().lower()
    if provider not in PROVIDERS:
        raise ProviderError(f"未知厂商 {provider!r}，可选: {'/'.join(PROVIDERS)}")
    api_key = _e("DOC2TC_API_KEY") or settings.get(f"api_key:{provider}", "")
    model = _e("DOC2TC_MODEL") or settings.get(f"model:{provider}", "")
    if provider == "custom":
        base_url = _e("DOC2TC_BASE_URL") or settings.get("base_url:custom", "")
        style = _e("DOC2TC_STYLE") or settings.get("style:custom", "openai")
        if not base_url:
            raise ProviderError("自定义端点缺少 Base URL：请在「设置」中补全")
        if not model:
            raise ProviderError("自定义端点缺少模型名：请在「设置」中补全")
    else:
        p = PROVIDERS[provider]
        base_url, style = p["base_url"], p["style"]
        model = model or p["default_model"]
    if not api_key:
        p = PROVIDERS[provider]
        hint = f"（获取 Key: {p['key_url']}）" if p.get("key_url") else ""
        raise ProviderError(f"未配置 {p['label']} 的 API Key：请在「设置」中保存{hint}"
                            f"（CLI 可用环境变量 DOC2TC_API_KEY）")
    try:
        return LLMConfig(api_key=api_key, base_url=base_url, model=model, style=style)
    except ValueError as e:  # pydantic ValidationError
        raise ProviderError(f"模型配置无效: {e}") from e
```

**Step 4: 跑测试**（`12 通过, 0 失败`）

**Step 5: Commit** `git commit -m "feat: resolve_llm_config 统一组装（env>settings 槽>预设默认，缺配显式报错）"`

---

### Task 4: store.py settings 新键集合 + 读取时迁移落库

**Files:**
- Modify: `core/store.py`（`DEFAULT_SETTINGS`、`settings_all`、新增 `settings_delete_keys`、顶部 import）
- Modify: `tests/test_core.py`（**改** `t_store_and_recover` 中老键用法；新增 `t_settings_migration`）

**Step 1: 修改既有测试 + 写新失败测试**

`t_store_and_recover` 中两行改为新键（旧键已不在白名单，保存会被忽略导致断言失败——这正是要先行修正的）:

```python
    s.settings_save({"api_key:glm": "sk-test-1234567890", "model:glm": "glm-5.3"})
    saved = s.settings_all()
    assert saved["api_key:glm"].startswith("sk-test")  # Store 层保存的是明文本体
```

以及末尾 `assert s2.settings_all()["model"] == "glm-4.6"` 改为 `assert s2.settings_all()["model:glm"] == "glm-5.3"`。

新增 `t_settings_migration`（db 级：老键落库迁移 + 旧键删除 + 未知端点归 custom）:

```python
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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
```

`__main__` 注册：`check("settings 老库迁移落库", t_settings_migration)`

**Step 2: 跑测试确认失败**（`t_settings_migration` FAIL 于 `got["provider"]` 仍为默认 "glm" 但 `api_key:glm` 空；`t_store_and_recover` 若未先改键也 FAIL）

**Step 3: 实现 `core/store.py`**

顶部加 `from . import providers`；`DEFAULT_SETTINGS` 整体替换为:

```python
DEFAULT_SETTINGS = {
    "provider": "glm",
    "rounds": 2,
    "model:glm": "glm-5.3",
    "model:deepseek": "deepseek-flash",
    "model:mimo": "mimo-v2.6-pro",
    "model:minimax": "MiniMax-M3.1-Flash",
    "model:custom": "",
    "api_key:glm": "",
    "api_key:deepseek": "",
    "api_key:mimo": "",
    "api_key:minimax": "",
    "api_key:custom": "",
    "base_url:custom": "",
    "style:custom": "openai",
}
```

`settings_all` 替换为:

```python
    def settings_all(self) -> dict:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        raw = {r["key"]: r["value"] for r in rows}
        # 老库一次性迁移：旧平铺四字段 -> 厂商槽，迁移结果落库并删除旧键
        legacy = providers.migrate_legacy_settings(raw)
        if legacy:
            self.settings_save(legacy)
            self.settings_delete_keys([k for k in
                                       ("api_key", "base_url", "model", "style") if k in raw])
            raw.update(legacy)
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in raw.items() if k in DEFAULT_SETTINGS})
        # rounds 存成 int
        try:
            merged["rounds"] = int(merged.get("rounds", 2))
        except (TypeError, ValueError):
            merged["rounds"] = 2
        return merged
```

`settings_save` 之后新增:

```python
    def settings_delete_keys(self, keys: list[str]) -> None:
        if not keys:
            return
        with self._lock, self._conn:
            self._conn.executemany("DELETE FROM settings WHERE key = ?", [(k,) for k in keys])
```

（`settings_save` 本体不用改：白名单 `k in DEFAULT_SETTINGS` 自动适配新键。）

**Step 4: 跑测试**（`13 通过, 0 失败`——重点看 `t_store_and_recover`、`t_settings_migration`）

**Step 5: Commit** `git commit -m "feat: settings 换厂商分槽键集（provider/model:/api_key:/custom 端点），读取时自动迁移老库并删旧键"`

---

### Task 5: web/app.py API 改造

**Files:**
- Modify: `web/app.py`（`_llm_config`、`health`、`get_settings`、`save_settings`、`_settings_view`、新增 `GET /api/providers`）

**Step 1: 实现**（无独立单测——此层为薄壳接线，Task 8 做端到端冒烟）:

import 区改为:

```python
from core.jobs import JobCanceled, process_job  # noqa: E402
from core.llm import LLMConfig  # noqa: E402
from core.parsers import SUPPORTED_EXTS  # noqa: E402
from core.providers import PROVIDERS, ProviderError, resolve_llm_config  # noqa: E402
from core.renderer import FORMATS  # noqa: E402
from core.store import Store  # noqa: E402
```

`_llm_config` 替换:

```python
def _llm_config() -> LLMConfig:
    try:
        return resolve_llm_config(store.settings_all())
    except ProviderError as e:
        raise HTTPException(400, str(e)) from e
```

`health` / `get_settings` / `save_settings` / `_settings_view` 替换:

```python
@app.get("/api/health")
def health() -> dict:
    s = store.settings_all()
    provider = s.get("provider", "glm")
    return {"ok": True, "version": "1.0.0", "provider": provider,
            "model": s.get(f"model:{provider}") or PROVIDERS[provider]["default_model"],
            "key_configured": bool(s.get(f"api_key:{provider}"))}


@app.get("/api/providers")
def get_providers() -> dict:
    """预设清单 + 每家 Key 配置状态（明文 Key 永不出现在任何响应里）。"""
    s = store.settings_all()
    return {"providers": [
        {"id": pid, "label": p["label"], "base_url": p["base_url"], "style": p["style"],
         "default_model": p["default_model"], "models": p["models"], "key_url": p["key_url"],
         "key_configured": bool(s.get(f"api_key:{pid}"))}
        for pid, p in PROVIDERS.items()]}


@app.get("/api/settings")
def get_settings() -> dict:
    return _settings_view(store.settings_all())


@app.post("/api/settings")
async def save_settings(payload: dict) -> dict:
    provider = str(payload.get("provider", "")).strip().lower()
    if provider:
        if provider not in PROVIDERS:
            raise HTTPException(400, f"未知厂商 {provider}")
    else:  # 未带 provider 时，Key/模型落在当前厂商
        provider = store.settings_all().get("provider", "glm")
    partial: dict = {"provider": provider}
    if payload.get("model") and str(payload["model"]).strip():
        partial[f"model:{provider}"] = str(payload["model"]).strip()
    if payload.get("api_key") and str(payload["api_key"]).strip() not in ("", "（未更改）"):
        partial[f"api_key:{provider}"] = str(payload["api_key"]).strip()
    if payload.get("base_url_custom") and str(payload["base_url_custom"]).strip():
        partial["base_url:custom"] = str(payload["base_url_custom"]).strip()
    if payload.get("style_custom") in ("openai", "anthropic"):
        partial["style:custom"] = payload["style_custom"]
    if str(payload.get("rounds", "")).strip():
        partial["rounds"] = str(payload["rounds"]).strip()
    return _settings_view(store.settings_save(partial))


def _settings_view(s: dict) -> dict:
    provider = s.get("provider", "glm")
    return {"provider": provider,
            "rounds": s.get("rounds", 2),
            "model": s.get(f"model:{provider}", ""),
            "model_by_provider": {pid: s.get(f"model:{pid}", "") for pid in PROVIDERS},
            "custom": {"base_url": s.get("base_url:custom", ""),
                       "style": s.get("style:custom", "openai")},
            "keys_configured": {pid: bool(s.get(f"api_key:{pid}")) for pid in PROVIDERS}}
```

**Step 2: 语法自检** Run: `python -c "import web.app"` Expected: 无输出（web.app 模块可导入；不启动服务）。

**Step 3: 跑全量测试**（`python tests/test_core.py`，`13 通过, 0 失败`——确认 store 改动未破坏）

**Step 4: Commit** `git commit -m "feat: Web 设置 API 改厂商预设制（/api/providers 清单、分槽保存、缺 Key 400 带获取链接）"`

---

### Task 6: 前端设置对话框改造（index.html）

**Files:**
- Modify: `web/static/index.html`（dialog HTML + 设置相关 JS）

**Step 1: 实现**

`<dialog id="dlg">` 整体替换为:

```html
<dialog id="dlg">
  <form class="dlg-body" id="settingsForm" method="dialog">
    <h3>模型设置</h3>
    <p class="tip">选择厂商后只需粘贴该家的 API Key——各厂 Key 分开保存，来回切换不用重填。
      Key 仅存本机 <code>data/doc2tc.db</code>，不会上传到任何地方。</p>
    <div class="field"><label>服务商</label>
      <select id="sProvider"></select></div>
    <div class="field"><label>模型（下拉选择，或手动输入任意模型 ID）</label>
      <input id="sModel" list="modelList" autocomplete="off">
      <datalist id="modelList"></datalist></div>
    <div class="field"><label>API Key <a id="keyUrl" target="_blank" rel="noopener"
        style="float:right;font-size:12px"></a></label>
      <input type="password" id="sKey" placeholder="粘贴该厂商的 API Key" autocomplete="off"></div>
    <div id="customFields" style="display:none">
      <div class="field"><label>Base URL（OpenAI 或 Anthropic 兼容端点）</label>
        <input id="sBase" placeholder="https://your-endpoint/v1"></div>
      <div class="field"><label>接口风格</label>
        <select id="sStyle">
          <option value="openai">OpenAI 兼容（/chat/completions）</option>
          <option value="anthropic">Anthropic 风格（/v1/messages）</option>
        </select></div>
    </div>
    <div class="dlg-foot">
      <button type="button" id="dlgCancel">取消</button>
      <button type="button" class="primary" id="dlgSave">保存</button>
    </div>
  </form>
</dialog>
```

JS「设置」段（`loadSettings` 到 `$("#dlgSave").onclick` 整段）替换为:

```javascript
/* ---------------- 设置 ---------------- */
let PROVIDER_LIST = [], settingsCache = null;
async function loadSettings() {
  try {
    const [ps, s] = await Promise.all([
      (await fetch("/api/providers")).json(),
      (await fetch("/api/settings")).json()]);
    PROVIDER_LIST = ps.providers; settingsCache = s;
    $("#sProvider").innerHTML = PROVIDER_LIST.map(p =>
      `<option value="${esc(p.id)}">${esc(p.label)}${p.key_configured ? " ·Key已配" : ""}</option>`).join("");
    applyProvider(s.provider, s);
    const label = (PROVIDER_LIST.find(p => p.id === s.provider) || {}).label || s.provider;
    const keyOk = s.keys_configured && s.keys_configured[s.provider];
    $("#modelChip").textContent = s.model ? `${label} · ${s.model} · ${keyOk ? "Key 已配置" : "未配置 Key"}` : "—";
    $("#startHint").textContent = keyOk ? "" : "先在「设置」里保存当前厂商的 API Key";
  } catch (e) { console.error(e); }
}
function applyProvider(pid, s) {
  const p = PROVIDER_LIST.find(x => x.id === pid) || PROVIDER_LIST[0];
  $("#modelList").innerHTML = (p.models || []).map(m => `<option value="${esc(m)}">`).join("");
  $("#sModel").value = (s.model_by_provider && s.model_by_provider[pid]) || p.default_model || "";
  $("#sKey").value = "";
  $("#sKey").placeholder = p.key_configured ? "已配置（留空则不更改）" : "粘贴该厂商的 API Key";
  const ku = $("#keyUrl");
  if (p.key_url) { ku.href = p.key_url; ku.textContent = "获取 Key ↗"; ku.style.display = ""; }
  else ku.style.display = "none";
  $("#customFields").style.display = pid === "custom" ? "" : "none";
  if (pid === "custom") {
    $("#sBase").value = (s.custom && s.custom.base_url) || "";
    $("#sStyle").value = (s.custom && s.custom.style) || "openai";
  }
}
$("#sProvider").onchange = () => applyProvider($("#sProvider").value, settingsCache);
$("#btnSettings").onclick = () => { loadSettings(); $("#dlg").showModal(); };
$("#dlgCancel").onclick = () => $("#dlg").close();
$("#dlgSave").onclick = async () => {
  const body = { provider: $("#sProvider").value, model: $("#sModel").value.trim() };
  if ($("#sKey").value.trim()) body.api_key = $("#sKey").value.trim();
  if (body.provider === "custom") {
    body.base_url_custom = $("#sBase").value.trim();
    body.style_custom = $("#sStyle").value;
  }
  const r = await fetch("/api/settings", { method: "POST", headers: { "content-type": "application/json" },
                                           body: JSON.stringify(body) });
  if (r.ok) { $("#dlg").close(); loadSettings(); }
  else alert("保存失败: " + (await r.json()).detail);
};
```

**Step 2: 冒烟**（Task 8 统一做浏览器级验证；此处先保证 Task 8 的 curl 冒烟可过）

**Step 3: Commit** `git commit -m "feat: 设置界面改厂商预设制——厂商下拉/模型 datalist/分槽 Key 占位/自定义折叠区"`

---

### Task 7: CLI doc2tc.py 接入预设

**Files:**
- Modify: `tools/doc2tc.py`

**Step 1: 实现**

docstring 更新 + `build_llm_config` 替换:

```python
"""doc2testcases CLI 外壳：需求文档 -> 测试用例（五格式）。

用法（工作目录为仓库根）:
    python tools/doc2tc.py 文档1 [文档2 ...] [--rounds N] [--out 目录]

厂商预设（内置 GLM/DeepSeek/MiMo/MiniMax，见 core/providers.py）:
    DOC2TC_PROVIDER=glm|deepseek|mimo|minimax|custom   # 缺省读已保存设置
API Key 读取顺序: 环境变量 DOC2TC_API_KEY > data/doc2tc.db 中对应厂商的 Key 槽（与 Web 共用）。
其它环境变量: DOC2TC_MODEL（当前厂商模型）；DOC2TC_BASE_URL / DOC2TC_STYLE（仅 custom 生效）。
"""
...
def build_llm_config() -> LLMConfig:
    import os
    from core.providers import ProviderError, resolve_llm_config
    settings = Store(BASE_DIR / "data" / "doc2tc.db").settings_all()
    try:
        return resolve_llm_config(settings, os.environ)
    except ProviderError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(2)
```

`main` 里现有的 `print(f"模型: {cfg.model} @ {cfg.base_url}…")` 行保持不变（Base URL 已能区分厂商，YAGNI）。顶部若缺 `import os` 则加入。

**Step 2: 验证失败路径** Run: `DOC2TC_PROVIDER=deepseek python tools/doc2tc.py samples/order-flow.pdf`
Expected: stderr 打印 `未配置 DeepSeek 的 API Key…（获取 Key: https://platform.deepseek.com/api_keys）…`，退出码 2（不发起 LLM 调用）。

**Step 3: 跑全量测试**（`13 通过, 0 失败`）

**Step 4: Commit** `git commit -m "feat: CLI 接入厂商预设（DOC2TC_PROVIDER，缺 Key 启动即检并给出获取链接）"`

---

### Task 8: 隔离实例端到端冒烟（不依赖真实 Key/网络 LLM）

**Files:** 无代码改动（验证任务）

**Step 1: 准备隔离数据目录并预置老形状配置**

```bash
export D2T=/tmp/d2t-smoke; rm -rf "$D2T"; mkdir -p "$D2T"
python - <<'EOF'
import sqlite3, os
db = os.path.join(os.environ["D2T"], "doc2tc.db")
c = sqlite3.connect(db)
c.executemany("INSERT INTO settings (key, value) VALUES (?, ?)",
              [("api_key", "sk-old-1234567890"),
               ("base_url", "https://open.bigmodel.cn/api/paas/v4"),
               ("model", "glm-4.6"), ("style", "openai")])
c.commit(); c.close()
EOF
```

**Step 2: 起隔离实例**（后台）Run: `DOC2TC_DATA_DIR=$D2T DOC2TC_PORT=48790 python web/app.py`

**Step 3: curl 冒烟（逐条核对）**

```bash
curl -s http://127.0.0.1:48790/api/health
# 期望 provider=glm, model=glm-4.6（老配置迁移而来）, key_configured=true

curl -s http://127.0.0.1:48790/api/providers
# 期望 5 家，glm 的 key_configured=true，其余 false；各带 models/default_model/key_url

curl -s http://127.0.0.1:48790/api/settings
# 期望 provider=glm, keys_configured.glm=true, 明文 Key 不出现

curl -s -X POST http://127.0.0.1:48790/api/settings -H "content-type: application/json" \
  -d '{"provider":"deepseek","model":"deepseek-v4-pro","api_key":"sk-ds-1234567890"}'
# 期望 provider=deepseek；随后 GET /api/health 的 key_configured=true、model=deepseek-v4-pro

curl -s http://127.0.0.1:48790/api/settings
# 期望 glm 的 Key 状态仍为 true（分槽保留）、deepseek 已配置

curl -s -X POST http://127.0.0.1:48790/api/jobs -F "files=@samples/order-flow.pdf" -F "rounds=1"
# 期望 400 且 detail 含 DeepSeek 获取 Key 提示？——注意此时 deepseek Key 已配，会真的发起 LLM 调用。
# 改为：先切回未配 Key 的厂商再试：
curl -s -X POST http://127.0.0.1:48790/api/settings -H "content-type: application/json" -d '{"provider":"mimo"}'
curl -s -X POST http://127.0.0.1:48790/api/jobs -F "files=@samples/order-flow.pdf" -F "rounds=1"
# 期望 400，detail 含「小米 MiMo」「platform.xiaomimimo.com」
```

**Step 4: 停隔离实例**（kill 后台任务）

**Step 5: 无独立提交**（验证任务；发现问题回上游 Task 修复后重跑）

---

### Task 9: 文档更新（README / CLAUDE.md）

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Step 1: README**

- 「快速开始」Web 方式注释改为：`首次使用先点右上角「设置」：选厂商（智谱 GLM / DeepSeek / 小米 MiMo / MiniMax / 自定义）→ 粘贴该家 API Key 即可；各厂 Key 分开保存，切换免重填`；
- CLI 段示例改为 `DOC2TC_PROVIDER=deepseek DOC2TC_API_KEY=sk-xxx python tools/doc2tc.py 需求文档.docx --rounds 2 --out out`；
- 「模型默认指向 BigModel…」段改为：`内置四家官方厂商预设（端点与最新模型清单见 core/providers.py，2026-11 核实），亦可切「自定义」接任意 OpenAI/Anthropic 兼容服务`。

**Step 2: CLAUDE.md**

- 「常用命令」CLI 注释补 `DOC2TC_PROVIDER=glm|deepseek|mimo|minimax|custom`；
- 「内核数据流」上方架构描述与「关键不变量」各加一句：预设注册表在 `core/providers.py`（端点/模型清单/迁移/组装唯一入口），两外壳经 `resolve_llm_config` 取配置，`llm.py` 传输层不感知厂商。

**Step 3: Commit** `git commit -m "docs: README/CLAUDE.md 更新厂商预设用法（四厂商 + DOC2TC_PROVIDER）"`

---

### Task 10: 全量回归 + 重启常驻实例收尾

**Step 1:** Run: `python tests/test_core.py` Expected: `13 通过, 0 失败`。
**Step 2:** 重启 48787 常驻实例（停旧任务 → `DOC2TC_PORT=48787 python web/app.py` 后台起 → `curl /api/health` 确认新形状 `provider` 字段出现）。
**Step 3:** `git status` 确认工作区干净，`git log --oneline` 核对提交序列。
**Step 4: Commit**（如有收尾改动）`git commit -m "chore: 厂商预设功能收尾"`（无改动则跳过）。

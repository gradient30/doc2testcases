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
        "base_url": "",
        "style": "openai",
        "default_model": "",
        "models": [],
        "key_url": "",
    },
}

_BY_BASE_URL = {v["base_url"].rstrip("/"): k for k, v in PROVIDERS.items() if k != "custom"}
_LEGACY_KEYS = ("api_key", "base_url", "model", "style")


class ProviderError(Exception):
    """厂商预设/配置错误（未知厂商、缺 Key、custom 配置不全）。"""


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

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

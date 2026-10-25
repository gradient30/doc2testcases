# -*- coding: utf-8 -*-
"""生成器：LLM 产出用例集，pydantic 门禁校验，失败带错误反馈有界重试。"""
from __future__ import annotations

from .llm import LLMConfig, LLMError, chat_json
from .parsers import ParsedDoc
from .prompts import GENERATOR_SYSTEM, build_generate_prompt
from .schema import CaseSet


class GenerationError(Exception):
    """生成失败：重试耗尽仍无法产出合法用例集。"""


def generate_caseset(cfg: LLMConfig, parsed: ParsedDoc, *, max_attempts: int = 3) -> CaseSet:
    """单文档生成用例集。校验失败把 pydantic 错误回灌给模型重试，最多 max_attempts 次。"""
    base_prompt = build_generate_prompt(parsed)
    prompt = base_prompt
    last_err: Exception | None = None
    for _ in range(max_attempts):
        try:
            data = chat_json(cfg, GENERATOR_SYSTEM, prompt)
            return CaseSet.model_validate(data)
        except LLMError:
            raise  # 网络/鉴权类错误不烧重试次数，直接抛给上层
        except Exception as e:  # pydantic ValidationError / 结构不符  # noqa: BLE001
            last_err = e
            prompt = (base_prompt
                      + f"\n\n注意：你上一次输出的 JSON 未通过结构校验：{str(e)[:800]}。"
                        "请修正后重新输出完整 JSON（字段结构与取值要求见上）。")
    raise GenerationError(f"{parsed.name}: {max_attempts} 次尝试均未通过校验: {last_err}")

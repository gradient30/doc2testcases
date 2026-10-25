# -*- coding: utf-8 -*-
"""评审与修订循环：独立评审 -> 有界修订（默认最多 2 轮），与 ZCode 工作流同构。

质量机制：评审与生成使用不同的 system 人设；approved 或仅剩 low 级意见即通过；
未通过但轮次耗尽时如实返回 approved=false，由上层标记（不静默放行）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .llm import LLMConfig, LLMError, chat_json
from .parsers import ParsedDoc
from .prompts import REVIEWER_SYSTEM, build_revise_prompt, build_review_prompt
from .schema import CaseSet, ReviewResult


class ReviewError(Exception):
    """评审环节失败（LLM 调用或输出不合法）。"""


@dataclass
class PipelineResult:
    caseset: CaseSet
    approved: bool
    rounds: int                      # 实际执行的评审轮数
    reviews: list[ReviewResult] = field(default_factory=list)
    revised: bool = False            # 是否发生过修订

    @property
    def open_findings(self) -> list:
        return self.reviews[-1].findings if self.reviews else []


def _review_once(cfg: LLMConfig, parsed: ParsedDoc, caseset: CaseSet) -> ReviewResult:
    try:
        data = chat_json(cfg, REVIEWER_SYSTEM, build_review_prompt(parsed, caseset))
        return ReviewResult.model_validate(data)
    except LLMError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ReviewError(f"{parsed.name}: 评审输出不合法: {e}") from e


def _revise_once(cfg: LLMConfig, parsed: ParsedDoc, caseset: CaseSet,
                 review: ReviewResult) -> CaseSet:
    from .generator import GenerationError  # 局部引入避免循环依赖
    try:
        data = chat_json(cfg, GENERATOR_SYSTEM, build_revise_prompt(parsed, caseset, review))
        return CaseSet.model_validate(data)
    except LLMError:
        raise
    except Exception as e:  # noqa: BLE001
        raise GenerationError(f"{parsed.name}: 修订输出不合法: {e}") from e


def run_pipeline(cfg: LLMConfig, parsed: ParsedDoc, *, rounds: int = 2) -> PipelineResult:
    """生成 -> (评审 -> 修订)* 有界循环。rounds 为修订轮数上限。"""
    from .generator import generate_caseset

    caseset = generate_caseset(cfg, parsed)
    reviews: list[ReviewResult] = []
    revised = False
    for _ in range(max(1, rounds + 1)):
        review = _review_once(cfg, parsed, caseset)
        reviews.append(review)
        if review.approved or all(f.severity == "low" for f in review.findings):
            return PipelineResult(caseset=caseset, approved=True,
                                  rounds=len(reviews), reviews=reviews, revised=revised)
        if len(reviews) > rounds:
            break  # 修订轮次已用尽，最后一轮评审仅作结论
        caseset = _revise_once(cfg, parsed, caseset, review)
        revised = True
    return PipelineResult(caseset=caseset, approved=False,
                          rounds=len(reviews), reviews=reviews, revised=revised)

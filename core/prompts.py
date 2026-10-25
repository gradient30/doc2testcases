# -*- coding: utf-8 -*-
"""LLM 提示词契约：生成 / 评审 / 修订。与 ZCode 工作流的提示词同源，输出改为直接返回 JSON。

改这里就改了两个外壳（CLI/Web）的行为，是规格变更的唯一入口之一（另一个是 core/schema.py）。
"""
from __future__ import annotations

import json

from .parsers import ParsedDoc
from .schema import CaseSet, ReviewResult

GENERATOR_SYSTEM = (
    "你是资深测试设计工程师，擅长从需求文档（md/pdf/docx/excel）提炼可测功能点并设计测试用例："
    "覆盖正向、边界、异常与规则组合，步骤可执行、预期可判定。只基于文档内容，不臆造不存在的功能；"
    "文档信息不足时在 notes 里说明假设。只输出合法 JSON，不输出任何解释文字。"
)

GENERATOR_USER = """请基于需求文档生成测试用例。

源文档：{doc_name}（{fmt} 格式）
{doc_body}

要求：
1. 先通读文档，提炼所有可测的功能点/业务规则（包括表格与附注里的规则、组合与互斥关系、上限、超时）。
2. 为每个功能点设计用例，覆盖：正向场景、边界值、异常/错误场景、规则组合。
3. 用例必须可执行：步骤具体、预期结果可判定。优先级：P0=核心功能正向；P1=重要功能与典型异常；P2=边界与次要场景。
4. document 字段填 "{doc_name}"，module 填业务模块名，id 在本文档内唯一（TC-01 起递增）。
5. 只输出如下结构的 JSON（不要围栏、不要解释）：
{{"document": "{doc_name}", "module": "业务模块名", "notes": "假设与说明，没有则空串", "cases": [{{"id": "TC-01", "title": "用例标题", "priority": "P0", "type": "功能测试", "preconditions": "前置条件", "steps": ["1. ...", "2. ..."], "expected": "预期结果", "source": "来源章节或规则ID，如 §2 / R03"}}]}}
若文档完全不含可测需求，返回 cases 为空数组并在 notes 说明，不要编造用例。{extra}"""

REVIEWER_SYSTEM = (
    "你是独立的测试用例评审员：对照原始需求逐条复查用例集，只找问题、不夸奖。"
    "意见要具体（缺哪条规则、哪个预期错了、哪步不可执行），让撰写员能直接修改。"
    "只输出合法 JSON，不输出任何解释文字。"
)

REVIEW_USER = """请独立复查一份测试用例集：只凭原始文档与用例集判断，只找问题。

原始需求文档：{doc_name}（{fmt} 格式）
{doc_body}

测试用例集（JSON）：
{cases_json}

逐项检查：
1. 覆盖：原文档每个功能点/规则（含组合、互斥、上限、超时）是否都有对应用例？列出遗漏。
2. 正确性：用例的预期结果是否与原文档一致？
3. 可执行性：步骤是否具体可操作、预期是否可判定？
4. 结构：P0/P1/P2 是否得当？字段是否完整、id 是否唯一？

可直接交付时 approved=true 且 findings 为空数组；否则 approved=false，每条意见一句话写清缺什么/错在哪。
severity：high=功能点遗漏或预期错误；medium=覆盖缺口或步骤不可执行；low=措辞、优先级等小问题。
只输出如下结构的 JSON：{{"approved": false, "findings": [{{"what": "一句话", "severity": "medium"}}]}}"""

REVISE_USER = """评审员对以下用例集提出如下意见：
{findings_json}

源文档：{doc_name}（{fmt} 格式）
{doc_body}

当前用例集（JSON）：
{cases_json}

请逐条处理意见：补充遗漏的用例、修正预期结果与步骤、调整优先级（low 级意见酌情处理），
保持原有 id 不变、新增用例追加到末尾。只输出修订后的完整 JSON（结构同当前用例集，含 document/module/notes/cases）。"""


def build_generate_prompt(parsed: ParsedDoc, *, extra: str = "") -> str:
    body = _doc_body(parsed)
    return GENERATOR_USER.format(doc_name=parsed.name, fmt=parsed.fmt, doc_body=body, extra=extra)


def build_review_prompt(parsed: ParsedDoc, caseset: CaseSet) -> str:
    cases_json = json.dumps(caseset.model_dump(), ensure_ascii=False, indent=1)
    return REVIEW_USER.format(doc_name=parsed.name, fmt=parsed.fmt,
                              doc_body=_doc_body(parsed), cases_json=cases_json)


def build_revise_prompt(parsed: ParsedDoc, caseset: CaseSet, review: ReviewResult) -> str:
    findings = [f.model_dump() for f in review.findings]
    cases_json = json.dumps(caseset.model_dump(), ensure_ascii=False, indent=1)
    return REVISE_USER.format(findings_json=json.dumps(findings, ensure_ascii=False, indent=1),
                              doc_name=parsed.name, fmt=parsed.fmt,
                              doc_body=_doc_body(parsed), cases_json=cases_json)


# 文档正文截断保护：超长文档只保留头部（提示词有长度上限，截断信息写进提示让模型知晓）
MAX_DOC_CHARS = 120_000


def _doc_body(parsed: ParsedDoc) -> str:
    text = parsed.text
    meta = f"（{'、'.join(f'{k}={v}' for k, v in parsed.meta.items())}）" if parsed.meta else ""
    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS] + "\n……（文档过长已截断）"
    return f"文档内容{meta}：\n{text}"

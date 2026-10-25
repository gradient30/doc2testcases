# -*- coding: utf-8 -*-
"""用例数据规格：canonical JSON schema 的 pydantic 契约（LLM 输出的唯一门禁）。

与 out/testcases/*.json 的字段一一对应，字段顺序与 renderer 的 CASE_FIELDS 保持一致。
renderer 对磁盘上的历史 JSON 仍走防御性兜底（dict 规范化），本模块只用于门禁 LLM 输出。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Priority = Literal["P0", "P1", "P2"]
Severity = Literal["low", "medium", "high"]

CASE_FIELDS = ["id", "title", "priority", "type", "preconditions", "steps", "expected", "source"]


class TestCase(BaseModel):
    """单条测试用例。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1, description="用例ID，如 TC-01，文档内唯一")
    title: str = Field(min_length=1, description="用例标题，一句话说清测什么")
    priority: Priority = Field(description="P0=核心功能正向；P1=重要功能与典型异常；P2=边界与次要场景")
    type: str = Field(default="", description="用例类型，如 功能测试")
    preconditions: str = Field(default="", description="前置条件")
    steps: list[str] = Field(default_factory=list, min_length=1, description="可执行步骤")
    expected: str = Field(min_length=1, description="预期结果，可判定")
    source: str = Field(default="", description="来源：文档章节或规则ID，如 §2 / R03")

    @field_validator("steps")
    @classmethod
    def _steps_not_blank(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v if s and s.strip()]
        if not cleaned:
            raise ValueError("steps 不能为空")
        return cleaned


class CaseSet(BaseModel):
    """一份文档对应的用例集（与 out/testcases/<doc>.json 同构）。"""

    model_config = ConfigDict(extra="ignore")

    document: str = Field(description="源文档标识（文件名或相对路径）")
    module: str = Field(min_length=1, description="业务模块名，如「用户登录」")
    notes: str = Field(default="", description="假设与说明：模糊需求、做出的假设；没有则空串")
    cases: list[TestCase] = Field(default_factory=list, description="用例列表；文档无可测需求时为空")

    @field_validator("cases")
    @classmethod
    def _ids_unique(cls, v: list[TestCase]) -> list[TestCase]:
        ids = [c.id for c in v]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"用例ID重复: {sorted(dup)}")
        return v

    def counts(self) -> dict[str, int]:
        return {
            "total": len(self.cases),
            "P0": sum(1 for c in self.cases if c.priority == "P0"),
            "P1": sum(1 for c in self.cases if c.priority == "P1"),
            "P2": sum(1 for c in self.cases if c.priority == "P2"),
        }

    def to_render_dict(self) -> dict:
        """转成 renderer 消费的 plain dict（document/module/cases，字段顺序固定）。"""
        return {
            "document": self.document,
            "module": self.module,
            "cases": [{f: getattr(c, f) for f in CASE_FIELDS} for c in self.cases],
        }


class ReviewFinding(BaseModel):
    what: str = Field(min_length=1, description="一句话：缺什么/错在哪")
    severity: Severity = Field(description="high=功能点遗漏或预期错误；medium=覆盖缺口；low=小问题")


class ReviewResult(BaseModel):
    approved: bool = Field(description="可直接交付为 true")
    findings: list[ReviewFinding] = Field(default_factory=list)

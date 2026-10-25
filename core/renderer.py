# -*- coding: utf-8 -*-
"""五格式渲染 + 回读校验。自 tools/render_all.py 平移，渲染函数保持逐字节一致的行为。

消费 plain dict（document/module/cases），对缺失字段防御性兜底——磁盘上的历史 JSON
不经过 pydantic，与此处兜底共同保证老输入永远可渲染。
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import xlrd
import xlwt
import yaml

# 用例字段（规范化后的固定顺序）
CASE_FIELDS = ["id", "title", "priority", "type", "preconditions", "steps", "expected", "source"]
HEADERS = ["用例ID", "模块", "标题", "优先级", "类型", "前置条件", "步骤", "预期结果", "来源"]
COL_WIDTHS = {0: 12, 1: 20, 2: 40, 3: 8, 4: 14, 5: 30, 6: 60, 7: 40, 8: 10}
WRAP_COLS = {2, 5, 6, 7}
SHEET_NAME_ILLEGAL = re.compile(r"[:\\/?*\[\]]")

FORMATS = ("xls", "yaml", "json", "md", "xmind")


class RenderError(Exception):
    """渲染或回读校验失败。"""


# ---------------------------------------------------------------- 基础工具

def as_text(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def normalize_case(raw):
    if not isinstance(raw, dict):
        raw = {}
    steps = raw.get("steps", [])
    if isinstance(steps, str):
        steps = [steps]
    elif not isinstance(steps, list):
        steps = []
    steps = [as_text(s) for s in steps]
    case = {f: as_text(raw.get(f, "")) for f in CASE_FIELDS if f != "steps"}
    case["steps"] = steps
    return {f: case[f] for f in CASE_FIELDS}


def normalize_doc(raw, filename="<json>"):
    if not isinstance(raw, dict):
        raise RenderError(f"{filename}: 顶层不是 JSON 对象")
    if not any(k in raw for k in ("document", "module", "cases")):
        raise RenderError(f"{filename}: 缺少 document/module/cases 字段, 不是用例文件")
    cases_raw = raw.get("cases", [])
    if not isinstance(cases_raw, list):
        cases_raw = []
    return {"document": as_text(raw.get("document", "")),
            "module": as_text(raw.get("module", "")),
            "cases": [normalize_case(c) for c in cases_raw]}


def load_documents(input_dir: Path) -> list[dict]:
    files = sorted(input_dir.glob("*.json"))
    if not files:
        raise RenderError(f"输入目录下没有 JSON 文件: {input_dir}")
    docs = []
    for fp in files:
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RenderError(f"读取 {fp.name} 失败: {e}") from e
        docs.append(normalize_doc(raw, fp.name))
    docs.sort(key=lambda d: d["document"])
    return docs


def sanitize_sheet_name(name):
    name = SHEET_NAME_ILLEGAL.sub("_", as_text(name)).strip()
    return name[:31] or "Sheet"


def unique_sheet_names(docs):
    used, result = set(), []
    for doc in docs:
        base = sanitize_sheet_name(doc["module"] or doc["document"] or "Sheet")
        candidate, n = base, 2
        while candidate in used:
            suffix = f"~{n}"
            candidate = base[: 31 - len(suffix)] + suffix
            n += 1
        used.add(candidate)
        result.append(candidate)
    return result


def total_cases(docs):
    return sum(len(d["cases"]) for d in docs)


# ---------------------------------------------------------------- 渲染（与历史实现逐行为一致）

def render_xls(docs, path):
    wb = xlwt.Workbook(encoding="utf-8")
    header_style = xlwt.XFStyle()
    header_font = xlwt.Font()
    header_font.bold = True
    header_style.font = header_font
    body_style = xlwt.XFStyle()
    wrap_style = xlwt.XFStyle()
    wrap_alignment = xlwt.Alignment()
    wrap_alignment.wrap = 1
    wrap_style.alignment = wrap_alignment
    for doc, sheet_name in zip(docs, unique_sheet_names(docs)):
        ws = wb.add_sheet(sheet_name)
        for col, width in COL_WIDTHS.items():
            ws.col(col).width = width * 256
        for c, header in enumerate(HEADERS):
            ws.write(0, c, header, header_style)
        for r, case in enumerate(doc["cases"], start=1):
            values = [case["id"], doc["module"], case["title"], case["priority"],
                      case["type"], case["preconditions"], "\n".join(case["steps"]),
                      case["expected"], case["source"]]
            for c, value in enumerate(values):
                ws.write(r, c, value, wrap_style if c in WRAP_COLS else body_style)
    wb.save(str(path))


def render_yaml(docs, path):
    payload = {"documents": docs}
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def render_json(docs, path):
    payload = {"documents": docs}
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")


def md_cell(value):
    text = re.sub(r"\r\n|\r|\n", "<br>", as_text(value))
    return text.replace("|", "\\|")


def render_md(docs, path):
    lines = ["# 测试用例汇总", ""]
    for doc in docs:
        lines.append(f"## {doc['module']}（{doc['document']}）")
        lines.append("")
        lines.append("| " + " | ".join(HEADERS) + " |")
        lines.append("|" + "|".join([" --- "] * len(HEADERS)) + "|")
        for case in doc["cases"]:
            cells = [case["id"], doc["module"], case["title"], case["priority"],
                     case["type"], case["preconditions"], "<br>".join(case["steps"]),
                     case["expected"], case["source"]]
            lines.append("| " + " | ".join(md_cell(c) for c in cells) + " |")
        lines.append("")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))


class IdCounter:
    def __init__(self, prefix):
        self.prefix = prefix
        self.n = 0

    def next(self):
        self.n += 1
        return f"{self.prefix}{self.n}"


def build_xmind_content(docs):
    doc_ids, case_ids, sub_ids = IdCounter("d"), IdCounter("c"), IdCounter("s")

    def topic(tid, title, children=None):
        if children is None:
            return {"id": tid, "class": "topic", "title": title}
        return {"id": tid, "class": "topic", "title": title, "children": {"attached": children}}

    doc_topics = []
    for doc in docs:
        case_topics = []
        for case in doc["cases"]:
            subs = [topic(sub_ids.next(), f"前置：{case['preconditions']}")]
            for step in case["steps"]:
                subs.append(topic(sub_ids.next(), step))
            subs.append(topic(sub_ids.next(), f"预期：{case['expected']}"))
            title = " ".join(p for p in (case["id"], case["priority"], case["title"]) if p)
            case_topics.append(topic(case_ids.next(), title, subs))
        doc_topics.append(topic(doc_ids.next(), doc["module"], case_topics))
    return [{"id": "sheet-1", "class": "sheet", "title": "测试用例",
             "rootTopic": {"id": "root", "class": "topic", "title": "测试用例汇总",
                           "children": {"attached": doc_topics}}}]


def render_xmind(docs, path):
    content = build_xmind_content(docs)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.json", json.dumps(content, ensure_ascii=False, indent=2))
        zf.writestr("manifest.json", json.dumps({"file-entries": {}}, ensure_ascii=False))
        zf.writestr("metadata.json",
                    json.dumps({"creator": {"name": "glm-test-workflow", "version": "1"}},
                               ensure_ascii=False))


# ---------------------------------------------------------------- 回读校验（与历史实现一致）

def verify_xls(path, docs):
    book = xlrd.open_workbook(str(path))
    if book.nsheets != len(docs):
        raise RenderError(f"sheet 数 {book.nsheets} != 文档数 {len(docs)}")
    total = 0
    for i, doc in enumerate(docs):
        sheet = book.sheet_by_index(i)
        expected_rows = len(doc["cases"]) + 1
        if sheet.nrows != expected_rows:
            raise RenderError(f"sheet[{i}] {sheet.name!r} 行数 {sheet.nrows} != 预期 {expected_rows}")
        total += sheet.nrows - 1
    return f"{book.nsheets} sheets / {total} cases"


def _verify_structure(data, docs, label):
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise RenderError(f"{label}: 顶层结构不是 {{'documents': [...]}}")
    parsed_docs = data["documents"]
    if len(parsed_docs) != len(docs):
        raise RenderError(f"{label}: 文档数 {len(parsed_docs)} != {len(docs)}")
    parsed_total = sum(len(d["cases"]) for d in parsed_docs
                       if isinstance(d, dict) and isinstance(d.get("cases"), list))
    expected_total = total_cases(docs)
    if parsed_total != expected_total:
        raise RenderError(f"{label}: 用例总数 {parsed_total} != 预期 {expected_total}")
    return f"{len(parsed_docs)} documents / {parsed_total} cases"


def verify_yaml(path, docs):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return _verify_structure(data, docs, "yaml")


def verify_json(path, docs):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return _verify_structure(data, docs, "json")


def verify_md(path, docs):
    text = Path(path).read_text(encoding="utf-8")
    if not text.strip():
        raise RenderError("内容为空")
    missing = [d["module"] for d in docs if d["module"] and d["module"] not in text]
    if missing:
        raise RenderError(f"缺少模块名: {missing}")
    table_lines = [ln for ln in text.splitlines() if ln.startswith("|")]
    data_rows = len(table_lines) - 2 * len(docs)
    expected_total = total_cases(docs)
    if data_rows != expected_total:
        raise RenderError(f"表格用例行数 {data_rows} != 预期 {expected_total}")
    return f"{len(docs)} modules / {data_rows} cases"


def verify_xmind(path, docs):
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        for entry in ("content.json", "manifest.json", "metadata.json"):
            if entry not in names:
                raise RenderError(f"zip 缺少条目 {entry}")
        content = json.loads(zf.read("content.json").decode("utf-8"))
    sheets = content if isinstance(content, list) else [content]
    if not sheets or not isinstance(sheets[0], dict):
        raise RenderError("content.json 不含有效 sheet")
    root = sheets[0].get("rootTopic") or {}
    doc_topics = ((root.get("children") or {}).get("attached")) or []
    if len(doc_topics) != len(docs):
        raise RenderError(f"文档层 topic 数 {len(doc_topics)} != 文档数 {len(docs)}")
    total = 0
    for doc_topic, doc in zip(doc_topics, docs):
        case_topics = ((doc_topic.get("children") or {}).get("attached")) or []
        if len(case_topics) != len(doc["cases"]):
            raise RenderError(f"文档 {doc['document']!r} 用例 topic 数 {len(case_topics)}"
                              f" != {len(doc['cases'])}")
        total += len(case_topics)
    return f"{len(doc_topics)} documents / {total} cases"


# ---------------------------------------------------------------- 主入口

@dataclass
class RenderReport:
    ok: bool
    files: dict = field(default_factory=dict)     # fmt -> Path
    lines: list[str] = field(default_factory=list)  # "xls OK 4 sheets / 114 cases"
    failures: list[str] = field(default_factory=list)
    documents: int = 0
    cases: int = 0

    def summary_line(self) -> str:
        return f"all OK: {self.documents} documents / {self.cases} cases"


def render_all(docs: list[dict], out_dir: Path, base_name: str = "TestCases") -> RenderReport:
    """渲染 + 校验主入口。docs 为 normalize_doc 之后的 plain dict 列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    steps = [
        ("xls", render_xls, out_dir / f"{base_name}.xls", verify_xls),
        ("yaml", render_yaml, out_dir / f"{base_name}.yaml", verify_yaml),
        ("json", render_json, out_dir / f"{base_name}.json", verify_json),
        ("md", render_md, out_dir / f"{base_name}.md", verify_md),
        ("xmind", render_xmind, out_dir / f"{base_name}.xmind", verify_xmind),
    ]
    report = RenderReport(ok=False)
    for name, render, path, verify in steps:
        try:
            render(docs, path)
        except Exception as e:  # noqa: BLE001
            report.failures.append(f"{name} 渲染失败: {e}")
            continue
        try:
            detail = verify(path, docs)
        except Exception as e:  # noqa: BLE001
            report.failures.append(f"{name} 校验失败: {e}")
            continue
        report.files[name] = path
        report.lines.append(f"{name} OK {detail}")
    report.documents = len(docs)
    report.cases = total_cases(docs)
    report.ok = not report.failures
    return report

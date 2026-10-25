#!/usr/bin/env python3
"""把 out/testcases/ 下所有用例 JSON 渲染为 5 种格式的汇总文件，并回读校验。

用法（工作目录为仓库根）:
    python tools/render_all.py

输入:  out/testcases/*.json   每份结构 {"document", "module", "cases": [...]}
输出:  out/TestCases.xls / .yaml / .json / .md / .xmind （覆盖写）
校验全部通过 -> 逐行打印各文件校验结果, 退出码 0
任一失败     -> 原因打印到 stderr, 退出码 1
脚本不访问网络。
"""

import json
import re
import sys
import zipfile
from pathlib import Path

import xlrd
import xlwt
import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
INPUT_DIR = BASE_DIR / "out" / "testcases"
OUT_DIR = BASE_DIR / "out"

OUT_XLS = OUT_DIR / "TestCases.xls"
OUT_YAML = OUT_DIR / "TestCases.yaml"
OUT_JSON = OUT_DIR / "TestCases.json"
OUT_MD = OUT_DIR / "TestCases.md"
OUT_XMIND = OUT_DIR / "TestCases.xmind"

# 用例字段（规范化后的固定顺序）
CASE_FIELDS = ["id", "title", "priority", "type", "preconditions", "steps", "expected", "source"]
# xls / md 表头
HEADERS = ["用例ID", "模块", "标题", "优先级", "类型", "前置条件", "步骤", "预期结果", "来源"]
# xls 列宽（字符数）: 0=用例ID 1=模块 2=标题 3=优先级 4=类型 5=前置条件 6=步骤 7=预期结果 8=来源
COL_WIDTHS = {0: 12, 1: 20, 2: 40, 3: 8, 4: 14, 5: 30, 6: 60, 7: 40, 8: 10}
# 需要自动换行样式的长文本列
WRAP_COLS = {2, 5, 6, 7}

SHEET_NAME_ILLEGAL = re.compile(r"[:\\/?*\[\]]")


# ---------------------------------------------------------------- 基础工具

def as_text(value):
    """任意值转字符串; None/缺失 -> 空串。"""
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def normalize_case(raw):
    """单条用例防御性兜底: 缺失字段 -> 空串/空数组。"""
    if not isinstance(raw, dict):
        raw = {}
    steps = raw.get("steps", [])
    if isinstance(steps, str):
        steps = [steps]
    elif not isinstance(steps, list):
        steps = []
    steps = [as_text(s) for s in steps]
    case = {field: as_text(raw.get(field, "")) for field in CASE_FIELDS if field != "steps"}
    case["steps"] = steps
    # 保持 CASE_FIELDS 顺序
    return {field: case[field] for field in CASE_FIELDS}


def normalize_doc(raw, filename):
    """单个文档对象防御性兜底。完全不像用例文件的 JSON 视为错误。"""
    if not isinstance(raw, dict):
        raise ValueError(f"{filename}: 顶层不是 JSON 对象")
    if not any(k in raw for k in ("document", "module", "cases")):
        raise ValueError(f"{filename}: 缺少 document/module/cases 字段, 不是用例文件")
    cases_raw = raw.get("cases", [])
    if not isinstance(cases_raw, list):
        cases_raw = []
    return {
        "document": as_text(raw.get("document", "")),
        "module": as_text(raw.get("module", "")),
        "cases": [normalize_case(c) for c in cases_raw],
    }


def load_documents():
    files = sorted(INPUT_DIR.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"输入目录下没有 JSON 文件: {INPUT_DIR}")
    docs = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"读取 {fp.name} 失败: {e}") from e
        docs.append(normalize_doc(raw, fp.name))
    docs.sort(key=lambda d: d["document"])
    return docs


def sanitize_sheet_name(name):
    """Excel sheet 名: 替换非法字符, 截断到 31 字符, 空名兜底。"""
    name = SHEET_NAME_ILLEGAL.sub("_", as_text(name)).strip()
    name = name[:31]
    return name or "Sheet"


def unique_sheet_names(docs):
    """每个文档一个 sheet 名(模块名优先, 缺失时退回文档名); 冲突时加 ~N 后缀。"""
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


# ---------------------------------------------------------------- 渲染

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
            values = [
                case["id"], doc["module"], case["title"], case["priority"],
                case["type"], case["preconditions"], "\n".join(case["steps"]),
                case["expected"], case["source"],
            ]
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
    """markdown 表格单元格: 换行 -> <br>, 转义竖线。"""
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
            cells = [
                case["id"], doc["module"], case["title"], case["priority"],
                case["type"], case["preconditions"], "<br>".join(case["steps"]),
                case["expected"], case["source"],
            ]
            lines.append("| " + " | ".join(md_cell(c) for c in cells) + " |")
        lines.append("")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines))


class IdCounter:
    """xmind topic 唯一 id 计数器。"""

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

    return [{
        "id": "sheet-1",
        "class": "sheet",
        "title": "测试用例",
        "rootTopic": {
            "id": "root",
            "class": "topic",
            "title": "测试用例汇总",
            "children": {"attached": doc_topics},
        },
    }]


def render_xmind(docs, path):
    content = build_xmind_content(docs)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.json", json.dumps(content, ensure_ascii=False, indent=2))
        zf.writestr("manifest.json", json.dumps({"file-entries": {}}, ensure_ascii=False))
        zf.writestr("metadata.json",
                    json.dumps({"creator": {"name": "glm-test-workflow", "version": "1"}},
                               ensure_ascii=False))


# ---------------------------------------------------------------- 校验

def verify_xls(path, docs):
    book = xlrd.open_workbook(str(path))
    if book.nsheets != len(docs):
        raise ValueError(f"sheet 数 {book.nsheets} != 文档数 {len(docs)}")
    total = 0
    for i, doc in enumerate(docs):
        sheet = book.sheet_by_index(i)
        expected_rows = len(doc["cases"]) + 1
        if sheet.nrows != expected_rows:
            raise ValueError(f"sheet[{i}] {sheet.name!r} 行数 {sheet.nrows} != 预期 {expected_rows}")
        total += sheet.nrows - 1
    return f"{book.nsheets} sheets / {total} cases"


def _verify_structure(data, docs, label):
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise ValueError(f"{label}: 顶层结构不是 {{'documents': [...]}}")
    parsed_docs = data["documents"]
    if len(parsed_docs) != len(docs):
        raise ValueError(f"{label}: 文档数 {len(parsed_docs)} != {len(docs)}")
    parsed_total = sum(len(d["cases"]) for d in parsed_docs
                       if isinstance(d, dict) and isinstance(d.get("cases"), list))
    expected_total = total_cases(docs)
    if parsed_total != expected_total:
        raise ValueError(f"{label}: 用例总数 {parsed_total} != {expected_total}")
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
        raise ValueError("内容为空")
    missing = [d["module"] for d in docs if d["module"] and d["module"] not in text]
    if missing:
        raise ValueError(f"缺少模块名: {missing}")
    # 表格数据行数 = 表格总行 - 表头 - 分隔行（每个文档一张表）
    table_lines = [ln for ln in text.splitlines() if ln.startswith("|")]
    data_rows = len(table_lines) - 2 * len(docs)
    expected_total = total_cases(docs)
    if data_rows != expected_total:
        raise ValueError(f"表格用例行数 {data_rows} != 预期 {expected_total}")
    return f"{len(docs)} modules / {data_rows} cases"


def verify_xmind(path, docs):
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        for entry in ("content.json", "manifest.json", "metadata.json"):
            if entry not in names:
                raise ValueError(f"zip 缺少条目 {entry}")
        content = json.loads(zf.read("content.json").decode("utf-8"))
    sheets = content if isinstance(content, list) else [content]
    if not sheets or not isinstance(sheets[0], dict):
        raise ValueError("content.json 不含有效 sheet")
    root = sheets[0].get("rootTopic") or {}
    doc_topics = ((root.get("children") or {}).get("attached")) or []
    if len(doc_topics) != len(docs):
        raise ValueError(f"文档层 topic 数 {len(doc_topics)} != 文档数 {len(docs)}")
    total = 0
    for doc_topic, doc in zip(doc_topics, docs):
        case_topics = ((doc_topic.get("children") or {}).get("attached")) or []
        if len(case_topics) != len(doc["cases"]):
            raise ValueError(
                f"文档 {doc['document']!r} 用例 topic 数 {len(case_topics)} != {len(doc['cases'])}")
        total += len(case_topics)
    return f"{len(doc_topics)} documents / {total} cases"


# ---------------------------------------------------------------- 主流程

def main():
    try:
        docs = load_documents()
    except Exception as e:
        print(f"render_all: 读取输入失败: {e}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    steps = [
        ("xls", render_xls, OUT_XLS, verify_xls),
        ("yaml", render_yaml, OUT_YAML, verify_yaml),
        ("json", render_json, OUT_JSON, verify_json),
        ("md", render_md, OUT_MD, verify_md),
        ("xmind", render_xmind, OUT_XMIND, verify_xmind),
    ]

    failures, ok_lines = [], []
    for name, render, path, verify in steps:
        try:
            render(docs, path)
        except Exception as e:
            failures.append((name, f"渲染失败: {e}"))
            continue
        try:
            detail = verify(path, docs)
        except Exception as e:
            failures.append((name, f"校验失败: {e}"))
            continue
        ok_lines.append(f"{name} OK {detail}")

    if failures:
        for name, reason in failures:
            print(f"render_all: {name} {reason}", file=sys.stderr)
        return 1

    for line in ok_lines:
        print(line)
    print(f"all OK: {len(docs)} documents / {total_cases(docs)} cases -> "
          f"{OUT_DIR / 'TestCases'}.*")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

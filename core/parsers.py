# -*- coding: utf-8 -*-
"""输入文档解析：md / pdf / docx / xlsx / xls -> 结构化文本。

提取配方与 ZCode 工作流中子代理使用的已验证方法一致，收敛为确定性代码。
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_EXTS = {".md", ".pdf", ".docx", ".xlsx", ".xls"}
# .doc/.ppt 等旧格式暂不支持，给出明确报错而不是静默失败
HINTS = {
    ".doc": "旧版 .doc 请先另存为 .docx",
    ".doc": "旧版 .doc 请先另存为 .docx",
    ".xlsm": "请另存为 .xlsx",
    ".csv": "请另存为 .xlsx",
}


class ParseError(Exception):
    """文档无法解析（损坏、加密、格式不符）。"""


@dataclass
class ParsedDoc:
    path: Path
    name: str  # 展示名（文件名）
    fmt: str  # md/pdf/docx/xlsx/xls
    text: str
    error: str = ""
    meta: dict = field(default_factory=dict)  # 页数/工作表数等补充信息


def _parse_md(path: Path) -> tuple[str, dict]:
    return path.read_text(encoding="utf-8", errors="replace"), {}


def _parse_pdf(path: Path) -> tuple[str, dict]:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ParseError("缺少 pypdf，请 pip install pypdf") from e
    try:
        reader = PdfReader(str(path))
        pages = []
        for i, page in enumerate(reader.pages, 1):
            t = (page.extract_text() or "").strip()
            if t:
                pages.append(f"【第{i}页】\n{t}")
        return "\n\n".join(pages), {"pages": len(reader.pages)}
    except ParseError:
        raise
    except Exception as e:
        raise ParseError(f"PDF 解析失败: {e}") from e


def _parse_docx(path: Path) -> tuple[str, dict]:
    try:
        import docx
    except ImportError as e:
        raise ParseError("缺少 python-docx，请 pip install python-docx") from e
    try:
        d = docx.Document(str(path))
        parts: list[str] = []
        # 按文档体顺序输出段落与表格
        from docx.document import Document as _Doc
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.ns import qn

        body = d.element.body
        para_map = {p._p: p for p in d.paragraphs}  # noqa: SLF001
        table_map = {t._tbl: t for t in d.tables}  # noqa: SLF001
        n_tables = 0
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                p = para_map.get(child)
                if p is not None and p.text.strip():
                    style = (p.style.name if p.style is not None else "") or ""
                    prefix = "# " if style.startswith("Heading 1") else ("## " if style.startswith("Heading") else "")
                    parts.append(prefix + p.text.strip())
            elif child.tag == qn("w:tbl"):
                t = table_map.get(child)
                if t is None:
                    continue
                n_tables += 1
                parts.append(f"【表格{n_tables}】")
                for row in t.rows:
                    cells = [c.text.strip().replace("\n", " ") for c in row.cells]
                    parts.append(" | ".join(cells))
        return "\n".join(parts), {"tables": n_tables, "paragraphs": len(d.paragraphs)}
    except ParseError:
        raise
    except Exception as e:
        raise ParseError(f"docx 解析失败: {e}") from e


def _parse_xlsx(path: Path) -> tuple[str, dict]:
    try:
        import openpyxl
    except ImportError as e:
        raise ParseError("缺少 openpyxl，请 pip install openpyxl") from e
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
        parts: list[str] = []
        n_sheets = 0
        for ws in wb.worksheets:
            n_sheets += 1
            parts.append(f"## 工作表: {ws.title}")
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c) for c in row]
                if any(c.strip() for c in cells):
                    parts.append(" | ".join(cells))
        wb.close()
        return "\n".join(parts), {"sheets": n_sheets}
    except ParseError:
        raise
    except Exception as e:
        raise ParseError(f"xlsx 解析失败: {e}") from e


def _parse_xls(path: Path) -> tuple[str, dict]:
    try:
        import xlrd
    except ImportError as e:
        raise ParseError("缺少 xlrd，请 pip install xlrd") from e
    try:
        book = xlrd.open_workbook(str(path))
        parts: list[str] = []
        for ws in book.sheets():
            parts.append(f"## 工作表: {ws.name}")
            for r in range(ws.nrows):
                cells = [str(ws.cell_value(r, c)) for c in range(ws.ncols)]
                if any(c.strip() for c in cells):
                    parts.append(" | ".join(cells))
        return "\n".join(parts), {"sheets": book.nsheets}
    except ParseError:
        raise
    except Exception as e:
        raise ParseError(f"xls 解析失败: {e}") from e


_PARSERS = {".md": _parse_md, ".pdf": _parse_pdf, ".docx": _parse_docx,
            ".xlsx": _parse_xlsx, ".xls": _parse_xls}


def parse_document(path: str | Path) -> ParsedDoc:
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        hint = HINTS.get(ext, "")
        raise ParseError(f"不支持的格式 {ext or '(无扩展名)'}；支持 {'/'.join(sorted(SUPPORTED_EXTS))}{'；' + hint if hint else ''}")
    if not p.is_file():
        raise ParseError(f"文件不存在: {p}")
    if ext == ".docx" and zipfile.is_zipfile(p) is False:
        raise ParseError("不是有效的 docx 文件")
    text, meta = _PARSERS[ext](p)
    text = text.strip()
    if not text:
        raise ParseError("文档解析后没有任何文本内容（可能是扫描件或空文件）")
    return ParsedDoc(path=p, name=p.name, fmt=ext.lstrip("."), text=text, meta=meta)

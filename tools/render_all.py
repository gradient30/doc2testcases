#!/usr/bin/env python3
"""把 out/testcases/ 下所有用例 JSON 渲染为 5 种格式的汇总文件，并回读校验。

薄壳：渲染与校验逻辑在 core/renderer.py（与 Web/CLI 共用同一内核）。
用法（工作目录为仓库根）: python tools/render_all.py [输入目录] [输出目录]
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.renderer import RenderError, load_documents, render_all  # noqa: E402


def main() -> int:
    input_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else BASE_DIR / "out" / "testcases"
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else BASE_DIR / "out"
    try:
        docs = load_documents(input_dir)
        report = render_all(docs, out_dir)
    except RenderError as e:
        print(f"render_all: 读取输入失败: {e}", file=sys.stderr)
        return 1
    for line in report.failures:
        print(f"render_all: {line}", file=sys.stderr)
    if not report.ok:
        return 1
    for line in report.lines:
        print(line)
    print(f"all OK: {report.documents} documents / {report.cases} cases -> "
          f"{out_dir / 'TestCases'}.*")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

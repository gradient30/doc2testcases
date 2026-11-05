#!/usr/bin/env python3
"""doc2testcases CLI 外壳：需求文档 -> 测试用例（五格式）。

用法（工作目录为仓库根）:
    python tools/doc2tc.py 文档1 [文档2 ...] [--rounds N] [--out 目录]

厂商预设（内置 GLM/DeepSeek/MiMo/MiniMax，见 core/providers.py）:
    DOC2TC_PROVIDER=glm|deepseek|mimo|minimax|custom   # 缺省读已保存设置
API Key 读取顺序: 环境变量 DOC2TC_API_KEY > data/doc2tc.db 中对应厂商的 Key 槽（与 Web 共用）。
其它环境变量: DOC2TC_MODEL（当前厂商模型）；DOC2TC_BASE_URL / DOC2TC_STYLE（仅 custom 生效）。
"""
import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core.jobs import process_job  # noqa: E402
from core.llm import LLMConfig  # noqa: E402
from core.providers import ProviderError, resolve_llm_config  # noqa: E402
from core.store import Store  # noqa: E402


def build_llm_config() -> LLMConfig:
    settings = Store(BASE_DIR / "data" / "doc2tc.db").settings_all()
    try:
        return resolve_llm_config(settings, os.environ)
    except ProviderError as e:
        print(str(e), file=sys.stderr)
        raise SystemExit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description="需求文档 -> 测试用例（五格式）")
    parser.add_argument("files", nargs="+", help="需求文档路径（md/pdf/docx/xlsx/xls）")
    parser.add_argument("--rounds", type=int, default=2, help="评审修订轮数上限（默认 2）")
    parser.add_argument("--out", default="out", help="输出目录（默认 out）")
    ns = parser.parse_args()

    files = [Path(f) for f in ns.files]
    missing = [f for f in files if not f.is_file()]
    if missing:
        print(f"文件不存在: {'、'.join(str(m) for m in missing)}", file=sys.stderr)
        return 2

    cfg = build_llm_config()
    out_dir = (BASE_DIR / ns.out).resolve() if not Path(ns.out).is_absolute() else Path(ns.out)
    job_dir = out_dir / ".job"
    print(f"模型: {cfg.model} @ {cfg.base_url}（{cfg.style}），修订轮数上限 {ns.rounds}")
    outcome = process_job(files, cfg, rounds=ns.rounds, job_dir=job_dir,
                          on_update=lambda docs, stage: print(f"[{stage}] "
                              + "、".join(f"{d.name}:{d.stage}" for d in docs)))

    print()
    for d in outcome.docs:
        if d.stage == "failed":
            print(f"✗ {d.name}: {d.error}")
        elif d.counts:
            badge = "评审通过" if d.approved else "仍有评审意见"
            print(f"✓ {d.name} -> 模块「{d.module}」{d.counts['total']} 条"
                  f"（P0 {d.counts['P0']}/P1 {d.counts['P1']}/P2 {d.counts['P2']}）[{badge}]")
            if d.notes:
                print(f"    备注: {d.notes[:200]}")
            for f in d.findings:
                print(f"    [{f['severity']}] {f['what']}")
    if outcome.render.files:
        for line in outcome.render.lines:
            print("  " + line)
    if outcome.render.ok:
        print(f"输出: {', '.join(str(p) for p in outcome.render.files.values())}")
        return 0 if all(d.stage == 'done' for d in outcome.docs) else 1
    if outcome.render.failures:
        for line in outcome.render.failures:
            print(f"渲染失败: {line}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

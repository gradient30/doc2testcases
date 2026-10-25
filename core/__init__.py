# -*- coding: utf-8 -*-
"""doc2testcases 内核：一个内核，两个外壳（CLI 与 Web）。

规格唯一契约在这里：
  parsers   输入文档 -> 结构化文本
  prompts   LLM 提示词契约（生成 / 评审 / 修订）
  llm       OpenAI 兼容 / Anthropic 风格 API 客户端
  generator LLM 生成用例 + pydantic 校验 + 有界重试
  reviewer  独立评审 + 有界修订循环
  renderer  五格式渲染 + 回读校验（与 tools/render_all.py 历史行为一致）
  jobs      面向单次任务的编排（解析->生成->评审->渲染）
  store     SQLite 任务/设置持久化（Web 外壳使用）
"""

__version__ = "1.0.0"

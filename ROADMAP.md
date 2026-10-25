# ROADMAP

## 已落地

- **P1 内核抽取**（v1.0.0）：`core/` 单一规格契约（schema/parsers/prompts/llm/generator/reviewer/renderer/jobs/store），CLI 与 Web 共用；渲染输出与历史工作流产物逐字节一致。
- **P2 Web 控制台**：FastAPI + 单页前端（上传 / 任务列表 / 阶段进度 / 评审意见展示 / 五格式下载 / 校验徽章 / SSE 事件流），`python web/app.py` 即用。
- **P3 加固**：SQLite 任务持久化、断点续跑（interrupted 标记 + 已完成文档复用）、API Key 本机保存（掩码显示）、限流/并发占线每 60s 自动重试、协作式取消、逐文档容错（单文档失败不影响其它文档渲染）。

## 未来计划（暂不实施）

### P4 · 轻量桌面端（记录备用）

用 **Tauri**（首选，体积小）或 **pywebview**（纯 Python，最省事）把现有 Web UI 打包为双击即用的桌面应用，面向没有 Python 环境的使用者。

要点与注意：
- 复用 `web/static/index.html` 与 `core/` 内核，桌面壳只负责窗口、侧加载（sidecar）启动 `web/app.py` 与自动打开浏览器视图，不复制任何业务逻辑。
- pywebview 路线：`pip install pywebview`，入口里 `webview.create_window("doc2testcases", "http://127.0.0.1:8787")` + 后台线程起 uvicorn；用 PyInstaller 打包单 exe。
- Tauri 路线：前端直接指向 sidecar 进程，需处理 Windows 签名与端口占用冲突（固定端口 + 探活）。
- 打包时把 `requirements.txt` 依赖打进运行时；`data/` 目录放到用户目录（`~/doc2testcases/`），避免装在 Program Files 后无写权限。
- 验收口径：与 Web 版同一内核，同一输入产出逐字节一致的五格式文件；离线安装包 < 50MB（pywebview）。

### 其他候选方向

- 用例模板自定义（字段/优先级分级可配）
- 需求文档差异比对后增量更新用例集
- 多人协作（任务队列服务化 + 账号体系）
- 导入存量用例做差距分析（对齐新需求）
- XMind/Excel 用例回读双向同步

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

doc2testcases：输入 `md / pdf / docx / xlsx / xls` 需求文档，经 LLM「生成 → 独立评审 → 有界修订」循环产出测试用例，渲染为 `.xls / .yaml / .json / .md / .xmind` 五种格式。

**一个内核，两个外壳**：`core/` 是唯一业务内核；CLI（`tools/doc2tc.py`）与 Web 控制台（`web/app.py`）都是薄壳，共用 `core.jobs.process_job`。同一输入在两个外壳下必须产出逐字节一致的五格式文件。全部注释与文档为简体中文，Python 3.10+，主要开发环境为 Windows。

## 常用命令

```bash
pip install -r requirements.txt

# 内核回归测试（不依赖 LLM / 网络；手写 runner，非 pytest）
python tests/test_core.py

# Web 控制台 → http://127.0.0.1:8787/
python web/app.py
#   隔离实例：DOC2TC_DATA_DIR=... DOC2TC_PORT=...

# CLI 全流程（Key 读取顺序：环境变量 DOC2TC_API_KEY > data/doc2tc.db 保存的设置）
python tools/doc2tc.py 文档1.docx 文档2.pdf --rounds 2 --out out
#   切厂商：DOC2TC_PROVIDER=glm|deepseek|mimo|minimax|custom
#   注意：--out 相对路径是相对仓库根（BASE_DIR）解析，不是当前目录

# 只重新渲染 + 回读校验（不需要 API Key）
python tools/render_all.py out/testcases out

# 本地 mock 模型端点（OpenAI 兼容，用于全链路机械验收；配合 Web 设置 base_url 指向它）
python tests/mock_llm_server.py [port]    # 默认 8790
```

测试是自包含脚本：每个用例是一个 `t_*` 函数，在 `__main__` 里逐个经 `check(name, fn)` 注册运行，任一失败退出码为 1。新增内核行为时在同文件补 `t_*` 函数 + `check(...)` 行；测试通过 monkeypatch `core.llm._chat_once` / `_http_transport` 模拟 LLM，不需要网络。

## 内核数据流（core/）

```
parse_document (parsers.py)          md/pdf/docx/xlsx/xls → 结构化文本
  → generate_caseset (generator.py)  LLM 生成 → CaseSet.model_validate 门禁，
                                      校验失败把 pydantic 错误回灌重试（≤3 次）
  → run_pipeline (reviewer.py)       (评审 → 修订)* 有界循环，rounds 默认 2；
                                      approved 或仅剩 low 级意见即通过，
                                      轮次耗尽仍不通过则如实返回 approved=false
  → 落盘 job_dir/testcases/<flat>.json   canonical JSON（续跑缓存）
  → render_all (renderer.py)         五格式渲染 + 回读校验（xls 用 xlrd 重开、
                                      yaml/json 解析、md 行数、xmind 解包查 topic 树），
                                      校验不过的格式不出现在 report.files（Web 端不可下载）
```

编排者是 `core/jobs.py: process_job`：文档级循环 + 阶段机（queued → parsing → generating → reviewing → rendering → done/failed）。单文档 ParseError/LLMError 只标记该文档 failed，不影响其它文档继续。持久化与 HTTP 均不在 jobs 内——由调用方经 `on_update` 回调写库、经 `cancel_check` 探针协作式取消（探针会下沉注入到 LLM 重试等待期，`AbortedError` 转为 `JobCanceled`）。

## 关键不变量（改动前必读）

- **渲染逐字节一致**：`core/renderer.py` 自历史 `tools/render_all.py` 平移，渲染行为与 `out/TestCases.*` 首版产物逐字节一致（验收记录以 MD5 比对）。修改渲染函数（字段顺序、`md_cell` 转义、xmind 结构、yaml dump 参数等）必须保持字节级兼容，或明确说明这是有意的行为变更。
- **规格唯一门禁**：LLM 输出的唯一校验入口是 `core/schema.py`（pydantic）。`renderer` 另有一套对磁盘历史 JSON 的防御性兜底（`normalize_doc`/`normalize_case` 消费 plain dict，不走 pydantic）——两套并存是有意设计：老输入永远可渲染。规格变更只有两个入口：`core/schema.py` 与 `core/prompts.py`（改提示词即同时改两个外壳的行为）。
- **厂商预设唯一入口**：`core/providers.py` 持有官方端点/模型清单（GLM/DeepSeek/MiMo/MiniMax + custom）、老配置迁移（`migrate_legacy_settings`）与统一组装（`resolve_llm_config`，优先级 env > settings 厂商槽 > 预设默认）；Web 与 CLI 都经它取 LLMConfig，`llm.py` 传输层不感知厂商。settings 键形如 `provider` / `model:<p>` / `api_key:<p>` / `base_url:custom` / `style:custom`，明文 Key 永不出现在 API 响应里。
- **LLM 错误分类**（`core/llm.py`）：`_RetryableLLMError`（限流 429、5xx、网络错误、并发占线 3009、额度窗口 1005、WAF 风控 3012）按 `retry_interval_s`（默认 60s）自动重试至多 `max_api_retries` 次；其余（401/403 鉴权、其它业务码、响应结构异常）抛硬 `LLMError`。**HTTP 200 内包业务错误封套 `{code, msg}` 必须识别为错误**，不得当成空成功——这是真实端到端踩过的坑，有回归测试（`t_llm_business_envelope`）。
- **传输降级链**：requests → urllib → （仅当 WAF 按 TLS 指纹拦截，即 403/405 且含 3012/unusual activity）降级系统 curl。普通端点永远走 requests。
- **续跑复用**：`process_job(resume=True)` 时 `job_dir/testcases/<flat>.json` 已存在且能通过校验的文档直接复用（Web 的「重试续跑」）；`fresh=true` 则删缓存重生成。进程重启后 `Store.recover_on_startup` 把 queued/running 任务标记 interrupted。
- **Shell 不含业务逻辑**：`web/app.py` 只做 HTTP 与任务生命周期（后台线程 + `_RUNNING` 注册表 + SSE 按 store version 轮询推送）；`tools/doc2tc.py` 只做参数解析与终端输出。新功能先进内核，外壳只接线。

## 数据与目录

- `data/`（gitignored）：SQLite 任务库 `doc2tc.db`（jobs + settings 两张表，明文存本机 API Key，API 返回时掩码）与 `data/jobs/<job_id>/{uploads,testcases,out}` 任务产物。
- `out/`：首版 ZCode 工作流产出的基线（4 文档 114 用例），是渲染一致性的比对基准，勿改动。
- `workflows/doc-to-testcases.dwf.ts`：历史 ZCode 动态工作流脚本，逻辑已收敛进 `core/`，仅作参考。
- `samples/`：样例文档（由 `make_fixtures.py` 生成）；`giraffe-bike/`：样例项目，其 README 是四份基线输入之一。

## 提交约定

提交信息用中文、conventional 前缀（`fix:` / `feat:` / `test:` / `docs:`），一行说清动机与影响（参见 git log 风格）。

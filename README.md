# doc2testcases — 需求文档自动生成测试用例

输入 `md / pdf / docx / xlsx` 需求文档，自动产出测试用例，并渲染为 **`.xls` / `.yaml` / `.json` / `.md` / `.xmind`** 五种格式。

**一个内核，两个外壳**：质量、规格、可靠只实现一次（`core/` 内核），CLI 与 Web 控制台都是它的薄壳——同一份输入在两个外壳下产出逐字节一致的五格式文件。

- 生成质量：每份用例集经「独立评审 → 有界修订（默认 2 轮）」循环，评审意见与假设在结果中显式呈现，不做黑盒；
- 规格唯一：用例 JSON 结构由 pydantic 契约（`core/schema.py`）门禁，渲染前回读校验（xls 用 xlrd 重开、yaml/json 解析、md 行数核对、xmind 压缩包解析），校验不过不提供下载；
- 可靠性：任务 SQLite 持久化，进程重启自动标记断点、已完成文档续跑复用；限流/并发占线每 60 秒自动重试；单文档失败不影响其它文档。

本仓库首版产物由 ZCode 动态工作流生成（4 份文档 → 114 条用例，见 `out/`），现已演化为可独立运行的双外壳工具。

## 快速开始

```bash
pip install -r requirements.txt

# 方式一：Web 控制台（推荐）
python web/app.py          # → http://127.0.0.1:8787/
#   首次使用先点右上角「设置」保存 API Key（仅存本机 data/doc2tc.db）

# 方式二：CLI
export DOC2TC_API_KEY=sk-xxx    # 或先在 Web 里保存过 Key
python tools/doc2tc.py 需求文档1.docx 需求文档2.pdf --rounds 2 --out out

# 只重新渲染（不需要 API Key）：
python tools/render_all.py out/testcases out

# 内核回归测试（不需要网络）：
python tests/test_core.py
```

模型默认指向 BigModel OpenAI 兼容端点（`glm-4.6`），设置里可切换任意兼容服务（Base URL / 模型 / OpenAI 或 Anthropic 风格）。

## 仓库结构

| 路径 | 说明 |
| --- | --- |
| `core/` | **内核**：schema（规格门禁）、parsers（四格式解析）、prompts（提示词契约）、llm（多风格客户端+重试）、generator/reviewer（生成与评审修订循环）、renderer（五格式渲染+回读校验）、jobs（任务编排）、store（SQLite 持久化） |
| `web/` | Web 外壳：`app.py`（FastAPI：上传/任务/SSE/下载/设置）+ `static/index.html`（单页控制台） |
| `tools/doc2tc.py` | CLI 外壳（全流程） |
| `tools/render_all.py` | 渲染薄壳（仅五格式渲染+校验，历史兼容入口） |
| `tools/wechat_notify.py` | 可选：任务完成后经 ZCode 本地微信机器人推送（仅自己机器可用） |
| `tests/test_core.py` | 内核回归测试（解析/门禁/渲染/持久化/失败路径） |
| `out/TestCases.*` | 首版工作流产物：4 文档 114 用例的五格式汇总 |
| `out/testcases/*.json` | 逐文档规范化用例集（渲染输入，可单独取用） |
| `workflows/doc-to-testcases.dwf.ts` | ZCode 动态工作流脚本（在 ZCode 里可复用整套流程） |
| `samples/` | 样例需求文档（docx/pdf/xlsx）及生成脚本 |
| `giraffe-bike/` | 样例项目（其 README 为首版四份输入之一） |
| `ROADMAP.md` | 路线图（P4 桌面端等未来计划） |

## 用例 JSON 结构

`out/testcases/` 下每个文件对应一份源文档（也是内核的 canonical 格式）：

```json
{
  "document": "samples/login-requirements.docx",
  "module": "用户登录",
  "notes": "假设与说明，没有则空串",
  "cases": [
    {
      "id": "TC-01", "title": "正确账号密码登录成功", "priority": "P0",
      "type": "功能测试", "preconditions": "已注册账号",
      "steps": ["1. 打开登录页", "2. 输入手机号与密码", "3. 点击登录"],
      "expected": "登录成功并跳转首页", "source": "§1"
    }
  ]
}
```

- `priority`：P0 = 核心功能正向；P1 = 重要功能与典型异常；P2 = 边界与次要场景。
- `source`：来源章节或规则 ID，便于回溯；`notes` 记录文档未明说处的假设。

## Web 控制台功能

- **输入**：拖拽上传多文档（自动识别格式，非法格式即时拒绝）
- **过程管理**：任务列表实时进度（解析 → 生成 → 评审/修订 → 渲染），每文档的阶段、用例数（P0/P1/P2）、评审结论、未决意见与假设逐项可见；运行中可取消；失败/中断可「重试续跑」（已完成文档复用结果）或「重新生成」
- **输出**：五种格式一键下载，附带回读校验徽章；校验未通过的格式不可下载
- **SSE 事件流**：`GET /api/jobs/{id}/events`（前端默认轮询，2s 刷新）

## API 一览（Web 外壳）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/jobs` | multipart 上传文件（`files` + `rounds`）创建任务 |
| GET | `/api/jobs` / `/api/jobs/{id}` | 任务列表 / 详情（含逐文档状态与评审意见） |
| GET | `/api/jobs/{id}/events` | SSE 实时事件 |
| POST | `/api/jobs/{id}/cancel` · `/retry?fresh=true` | 取消 · 续跑/重新生成 |
| GET | `/api/jobs/{id}/download/{xls\|yaml\|json\|md\|xmind}` | 下载（仅校验通过的格式） |
| GET/POST | `/api/settings` | 设置（Key 掩码返回，不明文回传） |

## 首版产出统计（ZCode 工作流）

| 输入文档 | 格式 | 模块 | 用例数 | 评审 |
| --- | --- | --- | --- | --- |
| samples/login-requirements.docx | docx | 用户登录 | 33（P0 4 / P1 17 / P2 12） | 通过 |
| samples/order-flow.pdf | pdf | 订单下单与履约流程 | 26（P0 7 / P1 10 / P2 9） | 通过 |
| samples/pricing-rules.xlsx | xlsx | 订单计价与优惠规则 | 23（P0 8 / P1 8 / P2 7） | 通过 |
| giraffe-bike/README.md | md | SVG 动画原型 | 32（P0 11 / P1 17 / P2 4） | 两轮修订后仍有少量意见（文档与实现偏差处已设计对录用例暴露） |

## 许可与依赖

Windows / macOS / Linux 均可运行（桌面端打包见 `ROADMAP.md` P4）。`data/` 目录（任务库与本机 Key）已被 `.gitignore` 排除，不会入库。

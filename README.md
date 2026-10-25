# doc2testcases — 需求文档自动生成测试用例

输入 `md / pdf / docx / xlsx` 需求文档，自动产出测试用例，并渲染为 **`.xls` / `.yaml` / `.json` / `.md` / `.xmind`** 五种格式。

本仓库是一次 [ZCode](https://zcode.z.ai) 动态工作流（dynamic workflow）的完整产物：4 份不同格式的需求文档 → **114 条测试用例**（P0/P1/P2 分级），每份用例集均由独立评审子代理对照原文档复查，五种输出格式经脚本回读校验（xls 用 xlrd 重开、yaml/json 解析、md 内容检查、xmind 压缩包解析）。

## 仓库结构

| 路径 | 说明 |
| --- | --- |
| `out/TestCases.xls` 等 | 五种格式的用例汇总（主交付，每文档一个 sheet/章节） |
| `out/testcases/*.json` | 各文档的规范化用例集（中间产物，也是渲染输入） |
| `tools/render_all.py` | 渲染器：把 `out/testcases/*.json` 渲染为五种格式并自动回读校验 |
| `workflows/doc-to-testcases.dwf.ts` | ZCode 动态工作流脚本，可在 ZCode 中复用整套「撰写+评审+渲染」流程 |
| `samples/` | 样例需求文档（docx/pdf/xlsx）及生成脚本 `make_fixtures.py` |
| `tools/wechat_notify.py` | 可选：任务完成后经 ZCode 本地微信机器人推送通知（仅在自己机器上可用，读取本机 `~/.zcode` 凭据） |
| `giraffe-bike/` | 样例项目（其 README 作为四份输入文档之一） |

## 用例 JSON 结构

`out/testcases/` 下每个文件对应一份源文档：

```json
{
  "document": "samples/login-requirements.docx",
  "module": "用户登录",
  "cases": [
    {
      "id": "TC-01",
      "title": "正确账号密码登录成功",
      "priority": "P0",
      "type": "功能测试",
      "preconditions": "已注册账号，密码正确",
      "steps": ["1. 打开登录页", "2. 输入手机号与密码", "3. 点击登录"],
      "expected": "登录成功并跳转首页",
      "source": "§1"
    }
  ]
}
```

- `priority`：P0 = 核心功能正向；P1 = 重要功能与典型异常；P2 = 边界与次要场景。
- `source`：用例来源（文档章节或规则 ID），便于回溯。

## 复用方式

1. **只复用渲染器**：把你的用例按上述结构放进 `out/testcases/`，运行 `python tools/render_all.py`，即得 `out/TestCases.{xls,yaml,json,md,xmind}`。依赖：`xlwt xlrd pyyaml openpyxl`（均 `pip install` 可得）。
2. **复用整套流程**：在 ZCode 中以 `workflows/doc-to-testcases.dwf.ts` 为脚本启动动态工作流，它会自动盘点工作区内的 md/pdf/docx/xlsx 文档，逐份「撰写 → 独立评审 → 修订」，再调用渲染器产出五种格式。

## 本次产出统计

| 输入文档 | 格式 | 模块 | 用例数 | 评审 |
| --- | --- | --- | --- | --- |
| samples/login-requirements.docx | docx | 用户登录 | 33（P0 4 / P1 17 / P2 12） | 通过 |
| samples/order-flow.pdf | pdf | 订单下单与履约流程 | 26（P0 7 / P1 10 / P2 9） | 通过 |
| samples/pricing-rules.xlsx | xlsx | 订单计价与优惠规则 | 23（P0 8 / P1 8 / P2 7） | 通过 |
| giraffe-bike/README.md | md | SVG 动画原型 | 32（P0 11 / P1 17 / P2 4） | 两轮修订后仍有少量意见（文档与实现偏差处已设计对录用例暴露） |

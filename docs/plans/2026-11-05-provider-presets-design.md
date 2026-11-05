# 设计：内置厂商预设（GLM / DeepSeek / MiMo / MiniMax）

日期：2026-11-05 · 状态：已批准 · 关联需求：设置里内置四家官方厂商预设，只填 Key 即用；自定义 OpenAI/Anthropic 兼容端点降为进阶选项。

## 背景与目标

现状设置是平铺四字段（API Key / Base URL / 模型 / 接口风格），手工填端点易错、模型名无从知晓。
目标：内置智谱 GLM、DeepSeek、小米 MiMo、MiniMax 四家官方预设（各自最新模型清单），用户只需粘贴该家 API Key；自定义兼容端点保留为第五个选项。

已确认的三个决策（用户拍板）：

1. **Key 按厂商分开保存**（每家一个 Key 槽，切换厂商自动带对应 Key，不重复粘贴）；
2. **Web 与 CLI 同步支持**，预设注册表放 `core/`（一个内核两个外壳）；
3. **全新安装默认 GLM / glm-5.3**（已保存过设置的老用户走迁移，不受影响）。

## 官方端点与模型清单（2026-11 核实，均官方 OpenAI 兼容）

| provider | base_url | 内置模型（官方 ID 原样） | 默认 |
| --- | --- | --- | --- |
| glm 智谱 | `https://open.bigmodel.cn/api/paas/v4` | glm-5.3、glm-5.3-flash、glm-5.2、glm-5.1、glm-5-turbo、glm-4.7、glm-4.7-flash（免费）、glm-4.6 | glm-5.3 |
| deepseek | `https://api.deepseek.com` | deepseek-flash、deepseek-v4-pro | deepseek-flash |
| mimo 小米 | `https://api.xiaomimimo.com/v1` | mimo-v2.6-pro、mimo-v2.6-flash | mimo-v2.6-pro |
| minimax | `https://api.minimaxi.com/v1` | MiniMax-M3.1-Flash、MiniMax-M3 | MiniMax-M3.1-Flash |
| custom | 用户自填（Base URL + style） | 无预设 | — |

备注：MiMo V2.5 系列已于 2026-10-21 官方下线，不收录；模型清单写死在代码里（YAGNI：不做远程拉取），前端模型框可手输任意 ID（datalist），厂商上新模型无需发版。

## 架构（方案 A：预设注册表进内核）

新增 `core/providers.py`（纯数据 + 两个函数，无 IO）：

- `PROVIDERS: dict[str, Provider]` —— label / base_url / style="openai" / default_model / models / key_url（获取 Key 的官方链接）；
- `resolve_llm_config(settings, env) -> LLMConfig` —— 统一组装：**环境变量 > settings 中该厂商 Key 槽与模型记忆 > 预设默认**；Key 缺失抛带厂商名与 key_url 的明确错误；
- `migrate_legacy_settings(raw) -> dict` —— 老四字段一次性迁移：base_url 匹配某家官方端点 → Key 落该家槽；匹配不到 → 归 custom 且保留原值（绝不丢 Key）。

`core/llm.py` 零改动（四家全部 OpenAI 兼容 chat/completions）。`web/app.py` 的 `_llm_config()` 与 `tools/doc2tc.py` 的 `build_llm_config()` 都改调 `resolve_llm_config`，壳内不再各拼配置。

CLI 环境变量：`DOC2TC_PROVIDER=glm|deepseek|mimo|minimax|custom`；`DOC2TC_API_KEY` / `DOC2TC_MODEL` 作用于当前 provider；`DOC2TC_BASE_URL` / `DOC2TC_STYLE` 仅 custom 生效。

## settings 数据形状（store.py）

KV 表不动，键集合改为：

```python
DEFAULT_SETTINGS = {
  "provider": "glm",
  "rounds": 2,
  "model:glm": "glm-5.3", "model:deepseek": "deepseek-flash",
  "model:mimo": "mimo-v2.6-pro", "model:minimax": "MiniMax-M3.1-Flash",
  "model:custom": "",
  "api_key:glm": "", "api_key:deepseek": "", "api_key:mimo": "",
  "api_key:minimax": "", "api_key:custom": "",
  "base_url:custom": "", "style:custom": "openai",
}
```

`settings_all()` 视图加工：`api_key:*` 明文**永不回传**，输出 `keys_configured: {provider: bool}`；`settings_save()` 白名单 = DEFAULT_SETTINGS 已列键。读取时发现旧键（`api_key`/`base_url`/`model`/`style`）先走 `migrate_legacy_settings` 落库。

## Web API 与前端

| 接口 | 变化 |
| --- | --- |
| `GET /api/providers`（新增） | 预设清单 + 每家 Key 配置状态 |
| `GET /api/settings` | 新形状：provider / rounds / models / custom{base_url,style} / keys_configured |
| `POST /api/settings` | `{provider?, model?, api_key?, base_url_custom?, style_custom?, rounds?}`；api_key 写当前 provider 槽 |
| `_llm_config()` | 改调 resolve_llm_config；未配 Key 时 400 指名厂商并附 key_url |

设置对话框：服务商下拉 → 模型下拉（datalist 可手输）→ API Key（占位符显示该家状态 + 「获取 Key ↗」链接）；仅选「自定义」时显示 Base URL / 接口风格两字段。顶栏 chip 显示 `厂商 · 模型 · Key 状态`。

## 错误处理

- 未配 Key：创建任务 400 / CLI 启动即退，指名厂商 + key_url；
- 401/403、429、业务封套（1005/3009 等）：复用 `llm.py` 现有分类与重试，预设层不加新逻辑；
- 迁移兜底：无法识别的老配置归 custom 保留原值。

## 测试（tests/test_core.py 手写 runner 追加）

1. `t_providers_registry`：注册表完整性（每家字段齐、default_model ∈ models、模型 ID 非空去重）；
2. `t_settings_migration`：bigmodel 老配置 → glm 槽；未知端点 → custom 保留；
3. `t_resolve_llm_config`：settings 组装 / 环境变量覆盖 / Key 缺失报错；
4. 现有 9 项回归全部保持通过。

## 文档与不做的事

- README 快速开始与设置章节更新；CLAUDE.md 内核模块清单补 `providers.py`；
- 不做（YAGNI）：模型清单远程更新、每任务级厂商选择、Key 加密存储（维持本机明文 + 掩码回传）。

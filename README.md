# pddbot

面向拼多多商家工作台（`mms.pinduoduo.com/chat-merchant`）的**客服消息监听与自动回复**工具：基于 **Playwright** 驱动浏览器、**LangGraph / LangChain** 调用大模型、**PySide6** 提供桌面配置界面，状态与映射保存在本地 **SQLite**。

> **完整使用说明、目录结构、探查脚本与注意事项**见 **[`md/README.md`](md/README.md)**。本页为访客快速入口。

---

## 功能概览

- 监听聊天相关 HTTP 与页面事件，对新消息做去重与流水线处理（会话激活 → 订单上下文 → 规则阶段 → LLM → 发送或干跑记录）。
- 按业务阶段（咨询、引导核销、执行核销、发资料等）切换策略，敏感场景可转人工或静默。
- GUI 管理模型、商品–资料映射、风控参数、日志等；支持干跑（`DRY_RUN`）与总开关（`BOT_ENABLED`）。

---

## 与其它方案的对比（简述）

便于选型时快速对照；**不构成对任何商业产品的评价或背书**，各产品条款、价位与功能范围以其官网为准。下表第二列列举市面常见的 **闭源订阅类** 方案（如 **阿奇索**、**旺财自动发货**、**友云自动发货** 等），侧重自动发货 / 客服接待等场景，彼此定位与套餐并不完全相同，此处只归纳**共性**，便于和开源自建对照。

| 维度 | pddbot（本项目） | 典型商用方案（阿奇索、旺财自动发货、友云自动发货等） |
|------|------------------|------------------------------------------------------|
| **运行环境** | **macOS / Linux / Windows**（同一套脚本与 GUI） | 多数以 **Windows 客户端或指定运行环境为主**，跨平台与国产化桌面支持往往弱于自建浏览器自动化 |
| **费用** | **源码开源**（Apache 2.0）；运行成本主要为 **自选的 LLM API**（如 DeepSeek 按量计费）等 | 多为 **订阅 / 按店或按功能计费**；例如阿奇索常见宣传档位约在 **¥30/月·店** 量级，**旺财 / 友云等以各自套餐标价为准** |
| **数据与可控性** | 会话与配置在 **本地 SQLite**；可审计代码、自改策略与对接模型 | 闭源商业软件；功能边界、发货规则与数据流依厂商产品设计 |
| **维护方式** | 社区与自建维护；页面改版时需跟随调整选择器等 | 一般由厂商跟进适配；依赖续费、版本更新与服务周期 |

---

## 快速开始

| 平台 | 操作 |
|------|------|
| macOS / Linux | 在项目根目录执行 `./start.sh`，或双击 `start.sh` |
| Windows | 双击 `start.bat` |

脚本会引导安装 **[uv](https://github.com/astral-sh/uv)**、创建 Python 3.11 虚拟环境、安装依赖（[`requirements.txt`](requirements.txt)，镜像配置见 [`uv.toml`](uv.toml)）、安装 Playwright Chromium，并启动 GUI：`uv run python -m gui.app`。

手动步骤与更多选项（探查页面、离线 LLM 自检等）见 **[`md/README.md`](md/README.md)**。

### 使用指导（GUI）

<p align="center">
  <img src="./assets/@assets/guide.gif" alt="pddbot：首页选择登录方式并点击「启动机器人」" width="920" />
</p>

### 环境变量

复制模板并按说明填写：

```bash
cp .env.example .env
```

至少需要配置 **`DEEPSEEK_API_KEY`**（或通过 GUI 写入 settings）。常用开关：**`BOT_ENABLED`**、**`DRY_RUN`**，详见 [`.env.example`](.env.example)。

---

## 文档索引

| 文档 | 内容 |
|------|------|
| [`md/README.md`](md/README.md) | 主文档：启动、目录说明、登录态、探查脚本、商品映射、LLM 自检、风险说明 |
| [`md/architecture.md`](md/architecture.md) | 架构、状态机、模块划分、与 LangGraph 的集成 |
| [`md/gui.md`](md/gui.md) | 桌面端页面与数据流 |
| [`md/protocol.md`](md/protocol.md) | 接口与字段速查 |
| [`md/scripts.md`](md/scripts.md) | `scripts/` 探查工具说明 |
| [`md/assets.md`](md/assets.md) | 静态资源说明 |
| [`md/license.md`](md/license.md) | 开源许可（Apache 2.0）说明与全文入口 |
| [`md/contributing.md`](md/contributing.md) | 贡献指南：环境、PR、测试、安全披露 |
| [`md/community.md`](md/community.md) | 社区：加群（备注 PDDBOT）、自愿打赏二维码 |
| [`SECURITY.md`](SECURITY.md) | 漏洞报告方式（根目录，便于 GitHub Security） |
| [`NOTICE`](NOTICE) | 版权与主要第三方依赖许可提示 |

---

## 社区与支持

用微信扫描下方二维码 **添加好友**，申请时 **备注 `PDDBOT`**，通过后会视情况拉入交流群。更多说明与自愿打赏见 **[`md/community.md`](md/community.md)**。

<p align="center">
  <img src="./assets/@assets/gerenerweima.JPG" alt="微信扫码添加好友，备注 PDDBOT" width="240" />
</p>

---

## 技术栈（摘要）

Python 3.11 · Playwright · PySide6 · LangGraph / LangChain · SQLite · DeepSeek（OpenAI 兼容 API）

---

## 安全与隐私

- **项目性质**：本仓库为独立开源作品，**与拼多多及其关联方无授权、赞助或合作关系**；文档中出现的「拼多多」等名称仅用于说明所面向的网页环境。
- **勿随仓库或发行包泄露本地数据**：除 **`storage_state.json`**、**`.env`** 外，**`db/*.db`**（含会话与业务数据）、**`logs/`**、**`captures/`** 亦可能含店铺与买家相关信息；请勿提交到 Git，制作源码压缩包或 Release 前请确认未误夹带上述路径。
- **`storage_state.json`**（浏览器登录态）与 **`.env`** 含敏感信息，请勿提交到版本库（已在 `.gitignore` 中）。
- 数据库默认在 `db/pddbot.db`，请自行备份并限制访问权限。

---

## 免责声明

本项目仅供学习与交流；使用者需确保在**自有店铺、合规前提**下使用，并遵守平台规则与当地法律法规。自动化回复可能触发平台风控；当前版本**未实现**「按分钟配额 / 夜间时段不回复」等节流逻辑（`.env` 中相关变量仅为预留），发送链路仅有输入模拟等节奏控制，请自行评估频率与风险；因使用本项目导致的任何后果由使用者自行承担。

---

## License

本项目使用 **[Apache License 2.0](LICENSE)**。全文见根目录 [`LICENSE`](LICENSE)。**第三方依赖**的版权说明见 [`NOTICE`](NOTICE)。参与贡献前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md) 与 [`md/contributing.md`](md/contributing.md)；报告安全问题见 [`SECURITY.md`](SECURITY.md)。

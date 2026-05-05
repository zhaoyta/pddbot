# 贡献指南

感谢你愿意改进 **pddbot**。以下为协作约定与本地开发说明。

---

## 环境与仓库

- **Python**：3.11（与 `start.sh` / `uv` 脚本一致）
- **包管理**：推荐 **[uv](https://github.com/astral-sh/uv)**，依赖见根目录 [`requirements.txt`](../requirements.txt)、镜像见 [`uv.toml`](../uv.toml)
- **机密**：勿提交 `.env`、`storage_state.json`、`*.db`、日志目录内容；`.gitignore` 已默认忽略

---

## 本地运行

```bash
# 安装依赖并启动 GUI（也可用 ./start.sh）
uv sync
uv run playwright install chromium
cp .env.example .env   # 按需填写 DEEPSEEK_API_KEY 等
uv run python -m gui.app
```

离线自检 LLM 工具链（不调用真实模型密钥时可配合 smoke 环境变量）：

```bash
uv run python -m llm._smoke_test
```

更多命令见 [`md/README.md`](README.md) §5。

---

## 提交 Issue

- **Bug**：说明系统版本、Python 版本、复现步骤、期望 vs 实际行为；若涉拼多多页面/DOM，附脱敏截图或日志片段（勿贴密钥与完整 Cookie）
- **功能建议**：简述场景与期望行为即可

---

## Pull Request

1. **从小改动开始**：一次 PR 聚焦一类问题（修复 / 单功能），便于审阅
2. **说明动机**：PR 描述里写「解决了什么问题」或「链接哪个 Issue」
3. **自检**：本地能跑通相关路径；涉及 LLM/发送链路时尽量跑 `llm._smoke_test`
4. **风格**：与现有代码保持一致（命名、类型注解、日志用 loguru 等）；避免无关大重构
5. **许可**：提交即表示你同意以 **[Apache License 2.0](../LICENSE)** 授权你的贡献（与仓库一致）

---

## 代码与目录提示

| 区域 | 路径 |
|------|------|
| 主循环 / 消息流水线 | `bot.py` |
| 阶段决策 | `core/stage.py` |
| SQLite | `core/store.py` |
| LLM / Agent | `llm/` |
| Playwright 发消息 | `tools/messaging.py` |
| GUI | `gui/` |

架构总览见 [`architecture.md`](architecture.md)。

---

## 安全与负责任披露

若你发现**可利用的安全漏洞**（例如凭据泄露、远程代码执行），请勿在公开 Issue 中披露细节；请先阅读根目录 **[`SECURITY.md`](../SECURITY.md)**。首选 GitHub Security Advisories；否则可发私密邮件至 **zhaoyta@gmail.com**（主题建议含 `security`）。

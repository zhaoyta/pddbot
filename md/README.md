# pddbot —— 拼多多商家客服自动回复（Playwright + LangGraph + PySide6 GUI）

一个基于 Playwright 的拼多多商家工作台（`mms.pinduoduo.com/chat-merchant`）客服消息**监听 + 自动回复**机器人，提供桌面 GUI 配置/控制。

## 0. 一键启动

| 平台 | 命令 |
|---|---|
| macOS / Linux | 终端执行 `./start.sh`，或双击 `start.sh` |
| Windows | 双击 `start.bat` |

启动脚本(`start.sh` / `start.bat`)会自动:
1. 检查 `uv`,缺失则用官方一行脚本下载(`curl -LsSf https://astral.sh/uv/install.sh | sh`)
2. `uv venv --python 3.11` 创建/复用虚拟环境
3. `uv pip install -r requirements.txt` 同步依赖(清华源由 `uv.toml` 固化)
4. `uv run python -m playwright install chromium` 装浏览器内核(首次约 150 MB,后续秒过)
5. `uv run python -m gui.app` 启动桌面 GUI

> 无需提前装 Python(uv 会自己拉 CPython 3.11)。GUI 设计见 [`md/gui.md`](./gui.md)。

---

## 目录结构

```
pddbot/
├── start.sh / start.bat     # 一键启动入口(双击或终端跑):uv 装齐依赖 → uv run gui.app
│
├── bot.py                   # 主循环：队列消费、编排 session/订单/stage/LLM/发送
│
├── gui/                     # 桌面 GUI（PySide6）
│   ├── app.py               #   QApplication 主入口
│   ├── main_window.py       #   主窗口：左侧导航 + 右侧 Stack
│   ├── bot_worker.py        #   独立线程里 asyncio.run(bot.run)
│   └── pages/               #   首页/商品/模型/阶段/飞书/风控/应用/会话/日志 等
│
├── scripts/                 # 手动探查（详见 [md/scripts.md](./scripts.md)）
│   ├── explore.py           #   聊天页抓包 / DOM
│   └── explore_redeem.py    #   核销页探针
│
├── core/
│   ├── config.py            # 内置常量：ROOT、默认 URL、路径、.env 兜底
│   ├── store.py             # SQLite 状态库（含 catalog_item / settings / stage_config 等）
│   ├── settings.py          # 配置层：settings 表 > .env > core/config 默认
│   └── stage.py             # 规则状态机（stage 决策）
│
├── runtime/                 # Playwright：浏览器会话 + HTTP 响应监听适配
│   ├── browser.py
│   ├── network.py
│   └── stealth.py
│
├── llm/                     # LLM Agent 层（LangChain create_agent + DeepSeek）
│   ├── client.py / prompts.py / tools.py / agent.py / runner.py
│   └── _smoke_test.py
│
├── tools/                   # 页面副作用：DOM、通知、商品查询
│   ├── catalog.py           # 商品映射（catalog_item 表）
│   ├── notify.py            # 飞书 webhook
│   ├── messaging.py         # 聊天输入框发送
│   ├── session_dom.py       # 左侧会话列表点选
│   ├── orders_fetch.py      # 右侧订单 tab + userAllOrder
│   └── left_panel_scan.py   # sync 后左栏未回复/红点扫描
│
├── db/pddbot.db             # SQLite 数据库（自动生成,所有配置/映射/会话都在这）
├── assets/                  # 静态图片（card_code_guide.png 等）
├── captures/                # 探查产物（gitignore）
├── logs/                    # 运行日志（gitignore）
│
├── uv.toml                  # 固化清华 PyPI 镜像源
├── .env.example             # 环境变量模板（GUI 启动后会写到 settings 表）
├── requirements.txt
├── LICENSE                  # Apache License 2.0 全文
├── CONTRIBUTING.md          # 贡献入口（详见 md/contributing.md）
├── SECURITY.md              # 漏洞报告说明（GitHub Security）
├── NOTICE                   # 版权与主要第三方依赖许可提示
│
└── md/
    ├── README.md            # 本文档
    ├── contributing.md      # 贡献指南（环境、PR、安全披露）
    ├── license.md           # Apache 2.0 说明与 NOTICE 入口
    ├── gui.md               # GUI 设计（页面/字段/数据流）
    ├── architecture.md      # 整体架构 + LangGraph 集成
    ├── protocol.md          # 拼多多接口字段速查
    ├── scripts.md           # scripts/ 探查脚本说明
    ├── assets.md            # 静态资源说明
    └── community.md         # 社区：加群、打赏（对外）
```

---

## 1. 环境准备（统一使用 uv）

正常情况下不用手敲下面这些 —— 直接 `./start.sh` 全自动。手动来一遍也很简单:

```bash
# 1. 创建 Python 3.11 虚拟环境（uv.toml 已固化清华镜像源，无需手动 -i）
uv venv --python 3.11

# 2. 装依赖（首次会从清华源下载）
uv pip install -r requirements.txt

# 3. 装 Playwright Chromium 内核（约 150 MB，首次较慢）
uv run python -m playwright install chromium

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填上 DEEPSEEK_API_KEY 等
```

> 项目根目录有 `uv.toml`,固化了清华镜像源。所有 `uv pip install ...` / `uv run ...` 都会自动走清华源。
>
> **运行任何脚本统一用 `uv run python ...`**（不用 source .venv/bin/activate）。比如 `uv run python -m llm._smoke_test`、`uv run python scripts/explore.py`。

---

## 2. 登录态（`storage_state.json`）

- **日常**：用 `./start.sh` 启动 GUI，在浏览器里扫码进入聊天页；需要落盘时点首页 **「💾 立即保存登录态」**（运行中也会周期性保存）。
- **探查脚本**（`scripts/explore.py` 等）依赖根目录（或你在「应用」里配置的）有效 **`storage_state.json`**，请先通过 GUI 完成至少一次登录。

> ⚠️ `storage_state.json` 含登录凭证，已在 `.gitignore` 中，**勿提交**到仓库。

---

## 3. 探查页面结构（可选）

```bash
uv run python scripts/explore.py
```

启动后做这几件事，**全程保持浏览器窗口在前台**：

1. 打开聊天页，加载完成。
2. 点开任意一个未读会话，让消息历史可见。
3. **等真实客户发来 1~2 条新消息**（最重要，用来观察 ``sync/message``、``chat/list`` 等 HTTP 响应格式）。
4. 自己也可以正常手动回复一条，方便观察"发消息" 的请求格式。
5. 完成后按 `Ctrl+C` 结束抓取。

抓取产物位于 `captures/` 目录，文件名带本次时间戳：

| 文件 | 内容 |
|---|---|
| `chat_*.jsonl` | ★ **聊天相关接口**（`/plateau/chat/*` 等）请求和响应**完整保留** |
| `dom_probe_latest_*.json` | ★ DOM 探针，每 5 秒覆盖一次，**最终是用户操作完毕的状态** |
| `dom_probe_*.json` | DOM 探针的初始版本（页面刚打开时） |
| `http_*.jsonl` | 所有 `pinduoduo.com` 域名下 HTTP 请求 / 响应摘要（4KB 后截断） |
| `console_*.log` | 浏览器控制台日志，含注入的 `MutationObserver` 输出 |

> 👉 完整架构见 [`md/architecture.md`](./architecture.md)（状态机、Tools、SQLite 表、风控）。
>
> 👉 已知接口与字段定义详见 [`md/protocol.md`](./protocol.md)：
> - `/plateau/chat/list` —— 会话历史消息
> - `/latitude/order/userAllOrder` —— 当前客户的订单列表（用作 AI 上下文）
>
> 探查时**务必**按这个顺序操作：
> 1. 切 2~3 个会话（让 `chat/list` 接口被多触发几次）
> 2. 在每个会话右侧都点一下【最新订单 → 个人订单】（让 `userAllOrder` 被触发）
> 3. 等真客户来 1~2 条消息
> 4. **选中一个会话停在那里，让输入框处于可见状态**（这一步决定 `dom_probe_latest` 的质量，必须等 5 秒以上让覆盖生效）
> 5. `Ctrl+C` 结束
>
> 决策更新：**发送消息走 DOM 点击发送按钮**（避免风控），所以本次探查不需要去抓发送 API，但要把输入框 / 发送按钮 / 会话列表项的 selector 探清楚。

### 3.1 核销页单独探查（已知 URL：`/orders/order/verify`）

聊天页探查完后,**单独再跑一次核销页探查**(拿核销页的 selector 和"开始核销"那一刻的 API):

```bash
uv run python scripts/explore_redeem.py
```

操作 5 步：

1. 在【*券码】输入一个真实核销码
2. 点【获取订单信息】，等订单信息显示出来
3. 如有【核销门店】下拉，选一个
4. 点【开始核销】，等"核销成功"提示
5. `Ctrl+C` 退出

产物：

- `captures/redeem_dom_latest_*.json` —— 核销页 DOM 结构
- `captures/redeem_http_*.jsonl` —— 含"开始核销"接口的完整 HTTP 流量

---

## 4. 商品 ↔ 资料 映射管理

下单后客户拿到卡券码核销,核销成功后我们要给他对应商品的资料链接。**全部在 GUI「商品」页配置**:

- 启动 GUI(`./start.sh`)→ 左侧「🛍 商品」
- 「➕ 新增」录入一条:匹配类型(商品 ID / SKU ID / 关键字)+ 标题 + 网盘 URL + 提取码
- 「🔎 命中测试」可以直接预览整段网盘消息

数据存在 `db/pddbot.db` 的 `catalog_item` 表,查询优先级:`goods_id > sku_id > keyword(最长命中)`。

---

## 5. LLM 层离线自检

`llm/` 模块已经搭好(基于 LangGraph 1.x + DeepSeek),不依赖浏览器/网络也能验证:

```bash
uv run python -m llm._smoke_test
```

会跑 5 个测试:
1. 5 个 stage 的 prompts 完整
2. 各 stage 的 tools 列表正确（S2/S3/S4 各多一个专属工具）
3. `lookup_product_url(goods_id="928035245974")` 输出的网盘消息格式跟样本一致
4. `send_text` 工具调用会落到 SQLite `action_log` 表
5. 5 个 stage 的 LangGraph 都能编译(用 mock API key 也能过)

> LLM 工具在 **`bot` 传入 Playwright `page` 且非 DRY_RUN** 时会调用真实 DOM：
> `send_text` → `tools.messaging.send_chat_message`，
> `send_card_code_guide` → `send_card_code_guide`（教程图路径见 `core.config.CARD_CODE_GUIDE_IMAGE`），
> `submit_card_code` → `tools.redeem.submit_card_code`（独立新 Tab 打开核销页，选择器为启发式）。
> 无 `page` 或干跑时仍为内存 stub + `action_log`。

---

## 6. 路线图与待办

完整路线图见 [`md/architecture.md` §10](./architecture.md)。当前进度：

- ✅ 项目骨架、登录、探查脚本、配置层
- ✅ SQLite 状态库 + 商品映射(catalog_item 表) + GUI 商品页
- ✅ LLM Agent 层（LangGraph 1.x + 5 stage prompt + 工具接入 messaging/redeem）
- ✅ GUI:首页/日志/商品/模型/阶段/飞书 + 启动脚本(uv 一条龙)
- ✅ `tools/messaging.py`、`tools/redeem.py`（核销页选择器需随页面改版调整）
- ✅ `core/stage.py` 状态机决策器
- ✅ `bot.py` 主程序

---

## 7. 已知风险与注意事项

- **拼多多商家后台风控较严**，长时间高频自动回复有封号风险。当前实现中：
  - **未实现**：收到消息后「先随机等待 1.5～3.5 秒再回复」、全店/单 uid **每分钟条数上限**、**夜间时段静默**（`.env` 里 `REPLY_DELAY_*`、`RATE_LIMIT_*` 会映射进 `settings` 表，但 **`bot.py` 主循环尚未读取**，属预留字段）。
  - **已有**：`tools/messaging.py` 在输入框内逐字输入时有随机间隔（与「整句回复前等待」不是同一层）；浏览器启动温身延迟见 GUI「风控」页 / `browser.warmup_delay_*`。
  - **规则转人工**：`core/stage.py` 命中敏感词或售后等 → `S_HUMAN`，当前为 **飞书告警 + 写 `action_log`**，不向买家自动带固定「已转人工」话术（除非后续你在 LLM/工具里发送）。
- 页面 DOM / 接口可能随拼多多前端发版变动，定期重跑 `scripts/explore.py` 校准。
- `BOT_ENABLED=false` 是紧急止损总开关；`DRY_RUN=true` 让 LLM 生成回复但不实际发送，只写 `action_log` 供人工审核。
- 仅供 **自有店铺自用**，请遵守平台规则与相关法律法规。

---

## 社区与交流

加微信群：请先扫 **[`md/community.md`](community.md)** 中的好友码，申请备注 **`PDDBOT`**，通过后拉群。自愿打赏见同页。

---

## 参与贡献

见 **[`md/contributing.md`](contributing.md)**；漏洞报告见仓库根目录 **`SECURITY.md`**。

---

## 开源许可

本项目采用 Apache License 2.0，详见 [license.md](license.md) 与仓库根目录 `LICENSE`。

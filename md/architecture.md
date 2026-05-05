# 自动客服整体架构

> 业务场景：拼多多店铺【想要资料库】卖虚拟教程，客户付款后凭卡券码核销取资料。
> 自动客服需要按"咨询 → 引导核销 → 执行核销 → 发资料"四阶段顺序闭环，每阶段由 LLM + 受限 tools 完成。

**业务主链与代码模块的一页索引**（会话监听 / 进会话后决策回复 / 列表已处理跳过）：见 [core_flow_modules.md](./core_flow_modules.md)。

---

## 0. 关键决策与边界条件（已确认）

> 来自 review 阶段的决策，所有实现必须遵守。

| # | 场景 | 决策 |
|---|---|---|
| D1 | 单 uid 多笔订单未核销 | 只处理**最新一笔**（按 `orderTime` 倒序取第一） |
| D2 | 客户说"链接失效/打不开/没收到，再发一份" | **直接转人工**，不主动重发资料 |
| D3 | 客户有售后单（`afterSalesInfo != null` 或 `compensateInfo.status` 有值） | **立即转人工**（飞书告警）；`conv_state.silenced_until` 字段预留，**当前未写入静默截止时间** |
| D4 | 首次接待该 uid（`conv_state` 无记录） | 先发一句"您好，请问有什么可以帮您？"，**等下一条消息才走 stage** |
| D5 | 风控阈值（分钟配额 / 夜间静默） | **尚未实现**：`.env` 中 `REPLY_DELAY_*`、`RATE_LIMIT_*` 仅入库为 `rate.*`，`bot.py` 未读取 |
| D6 | 干跑模式 | 通过 `DRY_RUN=true` 切：LLM 正常生成回复但**不点发送**，只写 `action_log` 表，方便人工审核 |
| D7 | 总开关 | `BOT_ENABLED=false` 时整个 bot 进入待机状态，只监听不动作（出问题秒关） |
| D8 | 资料回复格式 | 严格按百度网盘原生分享格式（见 `tools/catalog.py` `to_message`） |
| D9 | 资料映射主键 | `goodsId` 优先，`skuId` 次之，关键字兜底 |
| D10 | 发送消息方式 | DOM 模拟点击发送按钮，不调发送 API |
| D11 | 核销方式 | 在新 tab 打开 `/orders/order/verify` 页面 DOM 操作 |

---

## 1. 状态机

每位客户（按 `uid` 区分）对应一台独立状态机；进入哪个 Stage 由**规则判定**，不交给 LLM 自由发挥。

### 1.1 Stage 定义

| Stage | 名称 | 触发条件 | 核心动作 |
|---|---|---|---|
| **S0** | 首次打招呼 | 该 uid 在 `conv_state` 表里**无记录**（D4） | 发"您好，请问有什么可以帮您？"，记录 uid，**不进入业务处理** |
| **S1** | 咨询 | 该 uid 无订单 | 介绍商品 / 答疑 / 引导下单 |
| **S2** | 下单未核销引导 | 有"已支付订单"且**最新一笔**（D1）未发过教程图 | 发送"如何获取核销码"图片 + 文案 |
| **S3** | 收到核销码 | 客户消息里**识别到合法核销码**且该码**未核销过** | 提取码 → 打开核销页 → 输入码 → 提交 |
| **S4** | 核销完成发资料 | 该 uid 有**已核销但未发资料**的订单（取最新一笔） | 按 `goodsId` 查映射，按百度网盘格式回复 |
| **S_HUMAN** | 转人工 | 触发 D2 / D3 等规则 | **不经 LLM**：飞书告警 + `action_log`；不向买家自动发固定话术；未调用 `silence_uid` |

> 同一条新消息进入时，**按 S_HUMAN → S0 → S3 → S4 → S2 → S1 优先级判定**：
> - 先看是否触发"必须转人工"硬规则（售后/退款/明确求重发）
> - 再看是不是首次接待
> - 再看消息含核销码（客户主动行为）
> - 再看是否处于 S4（订单已核销待发资料）
> - 再看是否处于 S2（订单已付未引导）
> - 都不是 → 兜底到 S1

### 1.2 状态判定伪代码

```python
def decide_stage(uid: str, latest_msg: dict, orders: list[dict],
                 store: LocalStore) -> tuple[Stage, dict]:
    text = (latest_msg.get("content") or "").strip()

    # ---- S_HUMAN（D2/D3）：必须转人工的硬规则 ----
    if any(kw in text for kw in ("失效", "打不开", "过期", "拿不到", "下载不了",
                                  "投诉", "12315", "差评", "曝光")):
        return Stage.S_HUMAN, {"reason": "客户索要重发或敏感词触发"}
    if any(o.get("afterSalesInfo") or
           (o.get("compensateInfo") or {}).get("status")
           for o in orders):
        return Stage.S_HUMAN, {"reason": "客户存在售后/退款单"}

    # ---- S0（D4）：首次接待打招呼 ----
    if store.get_last_msg_id(uid) is None:
        return Stage.S0_GREET, {}

    # ---- S3：客户发了核销码 ----
    code = extract_card_code(text)
    if code and not store.is_code_redeemed(code):
        return Stage.S3_REDEEM, {"code": code}

    # ---- 取该 uid 最新一笔订单（D1） ----
    orders_desc = sorted(orders, key=lambda o: o.get("orderTime") or 0,
                         reverse=True)
    latest_order = orders_desc[0] if orders_desc else None

    # ---- S4：最新一笔已核销但还没发资料 ----
    if latest_order and \
       store.is_order_redeemed(latest_order["orderSn"]) and \
       not store.is_order_delivered(latest_order["orderSn"]):
        return Stage.S4_DELIVER, {"order": latest_order}

    # ---- S2：最新一笔已付但还没发过引导图 ----
    if latest_order and \
       latest_order.get("payStatus") == 2 and \
       not store.is_guide_sent(latest_order["orderSn"]):
        return Stage.S2_GUIDE, {"order": latest_order}

    # ---- 兜底 S1：咨询 ----
    return Stage.S1_CONSULT, {"order": latest_order}
```

---

## 2. 模块划分

```
pddbot/
├── scripts/                  # 手动探查（见 md/scripts.md）
│   ├── explore.py            # 聊天页抓包 / DOM 探针
│   └── explore_redeem.py     # 核销页探针
│
├── runtime/                  # 浏览器运行时
│   ├── browser.py            #   启动浏览器、加载 storage_state、扫码兜底、注入反检测
│   ├── network.py            #   监听 chat/list、userAllOrder 响应，分发到 EventBus
│   └── selectors.py          #   会话列表 / 输入框 / 发送按钮 / 核销页 selector 集中管理
│
├── core/                     # 业务核心
│   ├── config.py             #   内置常量：ROOT、默认 URL、路径、.env 兜底
│   ├── events.py             #   EventBus + 事件类型定义（NewUserMessage / OrdersUpdated…）
│   ├── store.py              #   本地持久化（SQLite）：会话状态、已发引导、已核销码、已发资料
│   ├── stage.py              #   决策器：根据 uid + msg + orders + store 判 Stage
│   └── conversation.py       #   会话上下文（最近 N 轮对话 + 当前 Stage + 订单 summary）
│
├── tools/                    # LLM 可调用的工具（function calling）
│   ├── messaging.py          #   send_text / send_image / send_card_code_guide
│   ├── orders.py             #   list_user_orders / refresh_user_orders
│   ├── redeem.py             #   open_redeem_page / submit_card_code / verify_redeem_result
│   ├── catalog.py            #   lookup_product_url(goods_name | sku_id)
│   └── notify.py             #   escalate_to_human（转人工告警）
│
├── llm/
│   ├── client.py             #   LLM 调用封装（OpenAI / DeepSeek / 兼容 SDK）
│   ├── prompts.py            #   各 Stage 的系统 prompt
│   └── agent.py              #   ReAct 风格的工具调用循环（含最大轮次限制）
│
├── db/pddbot.db             #   所有动态数据：会话状态 / 配置 / 商品映射 / 聊天历史 / action_log
│
├── assets/
│   └── card_code_guide.png   #   "如何获取核销码"的引导图（Stage S2 发）
│
└── bot.py                    # 主程序：装配上面所有模块，长驻
```

---

## 3. 各 Stage 的 LLM 系统 prompt 与可用 tools

### 3.1 共用规则（写进所有 prompt）

```text
你是【想要资料库】拼多多店铺客服。回复必须遵守：
1. 简短、亲切、口语化中文，不超过 50 字。
2. 不要透露你是 AI；不要承诺超出店铺规则的事。
3. 当你不确定怎么处理时，调用 escalate_to_human 转人工。
4. 严格按工具返回的事实回复，不要编造订单号、链接、价格。
```

### 3.1.5 Stage S0（首次打招呼）

| 项 | 内容 |
|---|---|
| 上下文 | 仅当前消息 |
| 可用 tools | `send_text` |
| 目标 | 发一句"您好，请问有什么可以帮您？"。**不调用 LLM 也行**，模板即可 |
| 副作用 | `store.set_last_msg_id(uid, msg_id)` 标记已接待过 |

### 3.1.6 Stage S_HUMAN（转人工）

| 项 | 内容 |
|---|---|
| 触发 | `core/stage.decide` 返回 `S_HUMAN`（敏感词 / 售后单等） |
| 实际行为 | **`bot.py` 直接处理**：`notify.send_feishu`（转人工告警）+ `action_log`；**不调用** `llm_runner`，会话侧无固定「已转人工」文案（与早期设计稿不同） |
| DB | `silenced_until` 列存在，当前 upsert 仍为 `0`；`store.silence_uid` **未被主流程调用** |

> 提示：模型在其它 Stage 内也可主动调用工具 `escalate_to_human`，该路径走 LLM tools，与规则触发的 `S_HUMAN` 分支不同。

### 3.2 Stage S1（咨询）

| 项 | 内容 |
|---|---|
| 上下文 | 最近 5 轮对话；客户最新消息；店铺 FAQ |
| 可用 tools | `send_text`、`escalate_to_human` |
| 目标 | 答疑 / 引导下单 |

### 3.3 Stage S2（下单未核销引导）

| 项 | 内容 |
|---|---|
| 上下文 | 客户订单 summary（订单号、商品名、金额、付款时间） |
| 可用 tools | `send_card_code_guide`（发图）、`send_text`、`escalate_to_human` |
| 目标 | 发"如何获取核销码"引导图 + 一句确认文案 |
| 副作用 | 发送成功后 store.mark_guide_sent(orderSn) |

### 3.4 Stage S3（收到核销码 → 执行核销）

| 项 | 内容 |
|---|---|
| 上下文 | 提取出的核销码、对应订单 summary |
| 可用 tools | `open_redeem_page`、`submit_card_code(code)`、`verify_redeem_result(code)`、`send_text`、`escalate_to_human` |
| 目标 | 调起核销页 → 提交码 → 验证成功 → 给客户一句"核销成功，正在为您发送资料"占位 |
| 失败兜底 | 重试 1 次仍失败 → escalate_to_human |
| 副作用 | 成功后 store.mark_code_redeemed(code, orderSn) |

> ⚠️ "进入核销页面"具体是哪个 URL / 弹窗 / 按钮，**待用户确认或后续探查**。设计上把它抽象成 `tools.redeem` 模块，里面用 Playwright 在**第二个 page** 上完成（不打扰客服主页面）。

### 3.5 Stage S4（核销完成发资料）

| 项 | 内容 |
|---|---|
| 上下文 | 订单 summary（特别是 `goodsName / skuId`） |
| 可用 tools | `lookup_product_url(goods_name, sku_id)`、`send_text`、`escalate_to_human` |
| 目标 | 查映射 → 把资料链接 + 提取码发给客户 |
| 失败兜底 | 映射没命中 → `escalate_to_human` |
| 副作用 | store.mark_order_delivered(orderSn) |

---

## 4. 关键 Tools 详细签名

```python
# tools/messaging.py
def send_text(uid: str, text: str) -> dict:
    """走 DOM：先确保左侧选中 uid 对应的会话，输入框 type 文字，点发送。"""

def send_image(uid: str, image_path: str, caption: str | None = None) -> dict:
    """走 DOM：拖拽 / 点击图片按钮上传指定本地图片。"""

def send_card_code_guide(uid: str) -> dict:
    """快捷封装：发 assets/card_code_guide.png + 标准文案。"""

# tools/orders.py
def list_user_orders(uid: str) -> list[dict]:
    """优先读 store 缓存；过期 → 主动调 userAllOrder 重放。"""

def refresh_user_orders(uid: str) -> list[dict]:
    """强制刷新（DOM 点'个人订单'，或 page.request 直接调）。"""

# tools/redeem.py
def open_redeem_page() -> dict:
    """在 context 里新开一个 tab，跳转到核销页（URL 待确认）。"""

def submit_card_code(code: str) -> dict:
    """在核销页输入码、提交。返回 {success, message}。"""

def verify_redeem_result(code: str) -> dict:
    """读核销页结果区域，确认是否核销成功，必要时拿到订单号。"""

# tools/catalog.py
def lookup_product_url(goods_name: str | None = None,
                       sku_id: int | None = None,
                       goods_id: int | None = None) -> dict | None:
    """从 catalog_item 表查映射。优先 goodsId,再 skuId,再关键字命中(最长匹配)。"""

# tools/notify.py
def escalate_to_human(uid: str, reason: str) -> dict:
    """触发飞书 webhook 告警（可选）；不写 silenced_until。"""
```

---

## 5. 数据库设计（SQLite）

```sql
-- 会话级状态：每个客户当前进展
CREATE TABLE conv_state (
    uid             TEXT PRIMARY KEY,
    last_msg_id     TEXT,          -- 已处理过的最大 msg_id
    last_active_ts  INTEGER,
    silenced_until  INTEGER,       -- 预留：转人工后静默截止（当前主流程未写入）
    notes           TEXT
);

-- 订单级状态：每张订单的处理进度
CREATE TABLE order_state (
    order_sn        TEXT PRIMARY KEY,
    uid             TEXT,
    goods_name      TEXT,
    sku_id          INTEGER,
    pay_status      INTEGER,
    guide_sent_at   INTEGER,        -- S2 引导图已发
    redeemed_at     INTEGER,        -- S3 核销成功
    delivered_at    INTEGER,        -- S4 资料已发
    delivered_url   TEXT
);

-- 卡券码使用记录：去重 + 防重复核销
CREATE TABLE card_code (
    code            TEXT PRIMARY KEY,
    uid             TEXT,
    order_sn        TEXT,
    submitted_at    INTEGER,
    succeeded_at    INTEGER,
    error_msg       TEXT
);

-- 操作审计：每一次自动回复都留痕
CREATE TABLE action_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT,
    stage           TEXT,
    tool            TEXT,
    payload         TEXT,           -- JSON
    success         INTEGER,
    error_msg       TEXT,
    ts              INTEGER
);
```

---

## 6. 商品 → 资料 映射格式

数据存在 `db/pddbot.db` 的 `catalog_item` 表(见 §5),由 GUI「商品」页 CRUD。

每行字段:

| 字段 | 说明 |
|---|---|
| `match_type` | `goods_id` / `sku_id` / `keyword` |
| `match_value` | 商品 ID / SKU ID 数字串;关键字模式时用英文逗号分隔多个词,如 `"散打,S022"` |
| `share_body` | 发给客户的整段百度网盘话术（必填） |
| `product_url` | 可选，对外展示的资料链接；未填时运行时可从正文解析 |
| `description` | 可选，简短描述；未填时可用正文首行摘要 |

`tools.catalog.lookup()` 查询优先级:`goods_id > sku_id > keyword(最长命中)`。
命中后 `CatalogItem.product_url` / `description` 为显式字段与解析结果的合并（显式优先）。

**Agent 模式**：每次组装 LLM 用户消息时，`llm/runner.py` 会追加 `【店铺商品资料索引】`，列出 **全部** `catalog_item` 条目的类型、匹配值、链接与后台「描述」（不包含 `share_body` 全文），便于无订单时也能回答「有没有某类资料」；核销后发复制话术仍走 `lookup_product_url` 等工具。

---

## 7. 核销码识别规则

从已知样本看，客户发的 `104396161488` 是 **12 位纯数字**，但实际格式可能多样。先用宽松规则 + 后续抓样本收紧：

```python
import re
CARD_CODE_PATTERNS = [
    re.compile(r"\b\d{12}\b"),         # 12 位数字
    re.compile(r"\b\d{10,18}\b"),      # 10~18 位数字（兜底）
    re.compile(r"\b[A-Z0-9]{8,20}\b"), # 字母数字混合（先注释，按需启用）
]

def extract_card_code(text: str) -> str | None:
    text = text.strip()
    for p in CARD_CODE_PATTERNS:
        m = p.search(text)
        if m:
            return m.group(0)
    return None
```

> 先用规则提取，再让 LLM 在 Stage S3 里"复核一下这是不是核销码"作为二次确认。

---

## 7.5 登录态管理

GUI 首页提供两种启动模式(下拉选择,默认根据 `storage_state.json` 是否存在自动判断):

| 模式 | 行为 | 适用 |
|---|---|---|
| 使用上次扫码的登录态(reuse) | `BrowserSession` 加载 `storage_state.json`,直接进聊天页;若 cookies 已过期会被拼多多自动跳到 login 页,Bot 会自动等扫码完成后落盘 | 日常启动 |
| 重新扫码登录(fresh / `force_relogin=True`) | 不加载 `storage_state.json`,context 干净启动,首次跳到 login 页 → 等扫码 → 立即落盘 | 换号/cookies 有问题手动重置 |

支撑机制:
- `BrowserSession.start()` 检测 URL 含 `/login` → 调 `_wait_login_then_save`,等 `wait_for_function('!location.href.includes("/login")')` 后立即 `save_storage_state()`。
- `BrowserSession._periodic_save()` 每 5 分钟自动保存一次(防止意外退出丢失)。
- 首页「💾 立即保存登录态」按钮可手动触发。
- 首页「🗑 清除已保存的登录态」按钮删除 `storage_state.json`(Bot 停止时可用),会自动把下拉切到 fresh。
- 浏览器卡在 login 页时,主循环把 GUI 状态切到 `awaiting_login`,首页有醒目提示。

---

## 8. 风控与节流（与实现对齐）

| 维度 | 当前实现 |
|---|---|
| 回复前整段等待 | **未实现**：`rate.reply_delay_min/max` 未在 `bot.py` 使用 |
| 单 uid / 全店每分钟条数 | **未实现**：`rate.per_uid_per_min`、`rate.global_per_min` 未统计与拦截 |
| 夜间时段不回复 | **未实现** |
| 输入框节奏 | `tools/messaging.py` 等对按键/输入有随机间隔（≠ 整句回复前延迟） |
| 浏览器启动温身 | `browser.warmup_delay_*` + GUI「风控」页，经 `BrowserSession` 生效 |
| 规则转人工 | `core/stage.py` 敏感词、售后等 → `S_HUMAN` → 飞书 + 日志（见 §3.1.6） |
| 模型侧转人工 | 各 Stage 工具 `escalate_to_human` → 飞书 + `action_log` |

以下为**设计预留 / 未落地**：「连续 2 次工具失败自动 escalate」「转人工后静默 30 分钟」等需在 `bot.py` / `store` 层补充逻辑后方可宣称支持。

---

## 9. LangGraph 集成（LLM Agent 层）

> 业务硬规则在 `core/stage.py` 决策完后,把 (stage, context, deps) 交给 LLM 层。
> 每个 stage 用 `langchain.agents.create_agent`（langchain 1.x 推荐）独立成图,
> 该 stage 仅暴露其允许的工具子集,LangGraph 自动跑 LLM ↔ tool 循环。

```
┌──────────────────────────────────────────────────────┐
│ core/stage.py（业务规则,我们控制,不让 LLM 决策）      │
│   decide_stage(uid, msg, orders, store)              │
│   → ("S2_GUIDE", {"order": {...}})                   │
└────────────────────┬─────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────┐
│ llm/runner.py                                        │
│   run_stage(stage, context, deps) -> reply_text      │
│   · 组装 HumanMessage：店铺 QA +【店铺商品资料索引】+ 订单摘要 + …
│      （索引为全店映射的链接/描述，不含 share_body 全文）           │
│      ↓                                               │
│   llm/agent.py: build_agent(stage, deps)             │
│      ↓ create_agent(model, tools=stage_tools, prompt)│
│   ┌──────┐  tool_call?  ┌──────────┐                 │
│   │agent │──yes────────▶│ ToolNode │                 │
│   │ node │◀──result─────│          │                 │
│   └──┬───┘              └──────────┘                 │
│      │ no                                            │
│      ▼                                               │
│   AIMessage.content → 返回最终回复                    │
└──────────────────────────────────────────────────────┘
```

### 9.1 每 stage 的 tools 集合

| Stage | tools |
|---|---|
| S0_GREET | `send_text`, `escalate_to_human` |
| S1_CONSULT | `send_text`, `escalate_to_human` |
| S2_GUIDE | `send_card_code_guide`, `send_text`, `escalate_to_human` |
| S3_REDEEM | `submit_card_code`, `send_text`, `escalate_to_human` |
| S4_DELIVER | `lookup_product_url`, `send_text`, `escalate_to_human` |

### 9.2 deps 注入

LangChain `@tool` 装饰器的工具通过 **closure** 拿到运行时依赖,不读全局,方便单测：

```python
def make_stage_tools(stage: str, deps: dict) -> list:
    @tool
    def send_text(text: str) -> str:
        ...
        store = deps["store"]
        store.log_action(...)
    ...
```

deps 必填字段:`store`, `uid`, `stage`, `browser`(可空), `dry_run`(bool)。

### 9.3 当前实现状态

- ✅ `llm/client.py` `prompts.py` `tools.py` `agent.py` `runner.py` 与 Playwright 工具实现已接通（非 stub）。
- ✅ 离线 smoke：`python -m llm._smoke_test`
- ⏳ 分钟级节流、夜间静默、`silence_uid` 接入主循环等待实现（配置键已预留）。

---

## 10. 路线图

| 步骤 | 内容 | 状态 |
|---|---|---|
| 0 | 项目骨架、登录脚本、探查脚本 | ✅ |
| 1 | 跑探查抓 selector + 接口（核销页 URL 已知） | ⏳ 等数据 |
| 2 | `core/config.py` + `.env` + DeepSeek + BOT_ENABLED/DRY_RUN 开关 | ✅ |
| 3 | `tools/catalog.py` + 映射表(catalog_item DB 表) + GUI 商品页 | ✅ |
| 4 | `core/store.py`：SQLite 状态库（含 settings / stage_config / catalog_item） | ✅ |
| 5 | `llm/`：DeepSeek + LangGraph create_agent（5 个 stage） | ✅ |
| 5b | GUI:首页/日志/商品/模型/阶段/飞书 | ✅ |
| 6 | `core/stage.py`：状态机（D1~D7 决策） | ✅ |
| 7 | `tools/notify.py`：飞书 webhook 通知 | ✅ |
| 8 | `runtime/browser.py` `network.py`（及页面交互） | ✅（选择器随拼多多改版需维护） |
| 9 | `tools/messaging.py`：DOM 发送 | ✅ |
| 10 | 订单上下文：HTTP 拦截 + `orders_fetch` | ✅ |
| 11 | `tools/redeem.py`：核销页自动化 | ✅（选择器需维护） |
| 12 | `bot.py`：主循环装配 | ✅（分钟节流 / 夜间静默 / 显式对话锁仍待加） |
| 13 | 健康检查：每小时跑 selector 探针 | 待做 |
| 14 | 每日报表：23:00 汇总 action_log | 待做 |
| 15 | 先 DRY_RUN 跑 1~2 天审核质量 | 待做 |
| 16 | 全量上线 | 待做 |

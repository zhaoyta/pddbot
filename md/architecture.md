# 自动客服整体架构

> 业务场景：拼多多店铺【想要资料库】卖虚拟教程，客户付款后凭卡券码核销取资料。
> 自动客服需要按"咨询 → 引导核销 → 执行核销 → 发资料"四阶段顺序闭环，每阶段由 LLM + 受限 tools 完成。

---

## 1. 状态机

每位客户（按 `uid` 区分）对应一台独立状态机；进入哪个 Stage 由**规则判定**，不交给 LLM 自由发挥。

### 1.1 Stage 定义

| Stage | 名称 | 触发条件 | 核心动作 |
|---|---|---|---|
| **S1** | 咨询 | 该 uid 无订单 | 介绍商品 / 答疑 / 引导下单 |
| **S2** | 下单未核销引导 | 该 uid 有"已支付订单"且本订单**未发过教程图** | 发送"如何获取核销码"图片 + 文案 |
| **S3** | 收到核销码 | 客户消息里**识别到合法核销码**且该码**未核销过** | 提取码 → 打开核销页 → 输入码 → 提交 |
| **S4** | 核销完成发资料 | 该 uid 当前订单**已核销** | 按"订单商品名 / skuId → 资料链接" 映射回复 |

> 同一条新消息进入时，**按 S3 → S4 → S2 → S1 优先级判定**，确保顺序不被破坏：
> - 先看消息内容里是不是带核销码（S3 是用户主动行为）
> - 再看是否处于 S4（订单已核销待发资料）
> - 再看是否处于 S2（订单已付未引导）
> - 都不是 → 兜底到 S1

### 1.2 状态判定伪代码

```python
def decide_stage(uid: str, latest_msg: dict, orders: list[dict],
                 store: LocalStore) -> Stage:
    # ---- S3：客户发了核销码 ----
    code = extract_card_code(latest_msg["content"])
    if code and not store.is_code_redeemed(code):
        return Stage.S3_REDEEM, {"code": code}

    # ---- S4：当前 uid 有已核销但还没发资料的订单 ----
    pending_deliver = [
        o for o in orders
        if store.is_order_redeemed(o["orderSn"])
           and not store.is_order_delivered(o["orderSn"])
    ]
    if pending_deliver:
        return Stage.S4_DELIVER, {"order": pending_deliver[0]}

    # ---- S2：有已付订单但还没发过引导图 ----
    pending_guide = [
        o for o in orders
        if o.get("payStatus") == 2
           and not store.is_guide_sent(o["orderSn"])
    ]
    if pending_guide:
        return Stage.S2_GUIDE, {"order": pending_guide[0]}

    # ---- 兜底 S1：咨询 ----
    return Stage.S1_CONSULT, {}
```

---

## 2. 模块划分

```
pddbot/
├── login.py                  # 首次扫码登录
├── explore.py                # 抓 selector / 接口（已完成）
├── config.py                 # 全局常量
│
├── runtime/                  # 浏览器运行时
│   ├── browser.py            #   启动浏览器、加载 storage_state、注入反检测
│   ├── network.py            #   监听 chat/list、userAllOrder 响应，分发到 EventBus
│   └── selectors.py          #   会话列表 / 输入框 / 发送按钮 / 核销页 selector 集中管理
│
├── core/                     # 业务核心
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
├── catalog/
│   └── product_map.json      #   商品名 / skuId / goodsId → 资料链接 + 提取码
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
    """从 catalog/product_map.json 查映射。优先 skuId，再 goodsId，再模糊匹配 goodsName。"""

# tools/notify.py
def escalate_to_human(uid: str, reason: str) -> dict:
    """触发告警（控制台 / 邮件 / 微信机器人 / 钉钉），并在该会话静默 N 分钟。"""
```

---

## 5. 数据库设计（SQLite）

```sql
-- 会话级状态：每个客户当前进展
CREATE TABLE conv_state (
    uid             TEXT PRIMARY KEY,
    last_msg_id     TEXT,          -- 已处理过的最大 msg_id
    last_active_ts  INTEGER,
    silenced_until  INTEGER,       -- 转人工后的静默截止
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

`catalog/product_map.json`：

```json
{
  "by_sku_id": {
    "1876675563671": {
      "title": "【系统教学】散打基础教程视频版",
      "url": "https://pan.baidu.com/s/1Guz-8Lzmw-guvg3gByc7SQ",
      "pwd": "fdby"
    }
  },
  "by_goods_id": {
    "928035245974": { "title": "...", "url": "...", "pwd": "..." }
  },
  "by_keyword": [
    { "match": ["散打", "S022"], "url": "...", "pwd": "fdby" },
    { "match": ["单片机", "S010"], "url": "...", "pwd": "3ua3" }
  ]
}
```

`lookup_product_url` 查询优先级：`by_sku_id` > `by_goods_id` > `by_keyword`（模糊匹配最长命中）。

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

## 8. 风控与节流

| 维度 | 限制 |
|---|---|
| 单条消息延迟 | 收到新消息后随机 sleep 1.5~3.5 秒再回复 |
| 单 uid 频次 | 每 uid 最多每分钟回 3 条 |
| 全店频次 | 全店每分钟最多 30 条自动回复 |
| 夜间静默 | 23:00~08:00 默认不回复（可配置） |
| 错误转人工 | 同一 uid 连续 2 次工具调用失败 → escalate_to_human + 静默 30 分钟 |
| 黑名单关键词 | 客户消息含"投诉/曝光/12315/差评/官方介入" → 立刻转人工 |

---

## 9. 路线图

| 步骤 | 内容 | 状态 |
|---|---|---|
| 0 | 项目骨架、登录脚本、探查脚本 | ✅ |
| 1 | 用户跑探查抓 selector + 接口（含核销页 URL） | ⏳ 等数据 |
| 2 | `config.py` + `.env` + DeepSeek 配置 | ✅ |
| 3 | `tools/catalog.py` + `catalog_admin.py` 管理 CLI + 映射表模板 | ✅ |
| 4 | `core/store.py`：SQLite 状态库 | ✅ |
| 5 | `runtime/`：browser + network 监听 + selectors 落地 | ⏳ 等数据 |
| 6 | `core/stage.py`：状态机决策器 + 单测 | 待做 |
| 7 | `tools/messaging.py`：send_text / send_image / send_card_code_guide | ⏳ 等 selector |
| 8 | `tools/orders.py`：list / refresh | 待做 |
| 9 | `tools/redeem.py`：核销自动化 | ⏳ 等核销页探查 |
| 10 | `llm/`：DeepSeek 客户端 + prompt + agent loop | 待做 |
| 11 | `bot.py`：装配 + 主循环 + 节流 + 风控 | 待做 |
| 12 | 全量上线 + 观察调试 | 待做 |

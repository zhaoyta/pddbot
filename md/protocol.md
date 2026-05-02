# 拼多多商家客服 —— 接口协议笔记

> 持续整理过程中发现的拼多多商家工作台聊天相关接口与消息字段，作为后续 `listener` / `bot` 实现的参考依据。

---

## 1. 已知接口

### 1.1 拉取会话历史消息

- **URL**：`https://mms.pinduoduo.com/plateau/chat/list`
- **触发时机**：左侧选中一个会话时调用一次（待验证：是否会周期性轮询 / 切回时再次拉取）
- **未登录直接 GET**：返回 `{"error_code":43001,"error_msg":"会话已过期","success":false}`，说明依赖登录态 cookie。
- **响应主结构**：

```json
{
  "success": true,
  "result": {
    "response": "list",
    "request_id": 1777728433869,
    "result": "ok",
    "has_more": false,
    "read_mark": {
      "user_last_read": "1777719969994",
      "min_supported_msg_id": "1588908282573"
    },
    "messages": [ /* 见 §2 */ ],
    "cs_infos": []
  }
}
```

- `read_mark.user_last_read`：客户最后已读的 `msg_id`，用来判断"我们发的消息客户读了没"。
- `has_more`：是否还有更早的历史，分页加载用。

### 1.2 拉取当前客户的订单列表

- **URL**：`https://mms.pinduoduo.com/latitude/order/userAllOrder`
- **触发时机**：在聊天页右侧点击 `最新订单 → 个人订单` 时调用。
- **用途**：让 AI 回复时拿到客户的订单上下文（订单号、商品名、金额、状态、物流时间…）。
- **响应主结构**：

```json
{
  "success": true,
  "result": {
    "pageNo": 1,
    "pageSize": 10,
    "total": 1,
    "orders": [ /* 见 §3 订单字段 */ ]
  }
}
```

> 后续策略：每次有客户新消息进来时，**主动模拟点一下"个人订单"标签**（或直接用同一登录态的 `page.request.post` 发请求）来刷新该客户的订单列表，作为 AI 的上下文。

### 1.3 卡券核销页

- **URL**：`https://mms.pinduoduo.com/orders/order/verify`
- **页面 Tab**：`订单核销`
- **入口**：商家后台 → 发货管理 → **核销工具**

**页面元素（来自截图，selector 待 explore_redeem.py 补全）**：

```
┌─────────────────────────────────────────────────────────┐
│  订单核销                                                │
├─────────────────────────────────────────────────────────┤
│   *券码    [请输入____________]  [获取订单信息]           │
│   核销门店 [请选择 ▼]            [门店管理]               │
│   订单信息 ┌────────────────────────────────────┐       │
│           │ (输入券码后获取订单信息显示在这里)   │       │
│           └────────────────────────────────────┘       │
│   [开始核销]   [重置]                                   │
├─────────────────────────────────────────────────────────┤
│  核销记录                                                │
│  订单号 [请输入]    核销门店 [请选择▼]                    │
│  ...（表格）...                                          │
└─────────────────────────────────────────────────────────┘
```

**自动核销流程（伪代码）**：

```python
async def redeem(code: str) -> dict:
    page = await context.new_page()                 # 新 tab，不打扰客服主页
    await page.goto(REDEEM_PAGE_URL)

    await page.fill(CODE_INPUT_SELECTOR, code)       # 输入券码
    await page.click(GET_ORDER_INFO_BTN_SELECTOR)    # 点"获取订单信息"
    await page.wait_for_selector(ORDER_INFO_AREA_SELECTOR + ":not(:empty)",
                                 timeout=5000)
    # 可选：解析 ORDER_INFO_AREA 里的商品/金额做二次校验
    # 可选：选门店（如果店铺只有 1 个门店多半会自动）
    await page.click(START_REDEEM_BTN_SELECTOR)      # 点"开始核销"
    # 等成功提示（toast / 弹窗 / 表单清空 / 核销记录新增一行）
    await page.wait_for_function(
        "(() => /核销成功|成功/.test(document.body.innerText))",
        timeout=8000,
    )
    await page.close()
    return {"success": True, "code": code}
```

**关键 selector 占位（待 `explore_redeem.py` 抓取后填入）**：

| 含义 | 占位变量 | 数据来源 |
|---|---|---|
| 券码输入框 | `CODE_INPUT_SELECTOR` | redeem_dom_latest → "输入框候选"，placeholder="请输入" 那个 |
| 获取订单信息 按钮 | `GET_ORDER_INFO_BTN_SELECTOR` | redeem_dom_latest → "按钮"."获取订单信息" |
| 订单信息展示区 | `ORDER_INFO_AREA_SELECTOR` | redeem_dom_latest → "关键标签"."订单信息" 的下一个兄弟 / 父容器 |
| 核销门店下拉 | `STORE_SELECTOR` | redeem_dom_latest → "下拉选择候选" 中文字带"请选择"的 |
| 开始核销 按钮 | `START_REDEEM_BTN_SELECTOR` | redeem_dom_latest → "按钮"."开始核销" |
| 核销记录表格 | `RECORD_TABLE_SELECTOR` | redeem_dom_latest → "表格候选" |

> 同时 `explore_redeem.py` 会抓"开始核销"那一刻的 HTTP 请求（POST 到某 verify 接口），可作为后续直接调 API 的备选路径（暂不启用，统一走 DOM）。

### 1.4 发送消息（决策：走 DOM 模拟，不调 API）

> 决策：**不直接调发送 API**，避免被风控识别为脚本流量。统一走"页面发送按钮"模拟真人操作。

发送流程伪代码：

```python
# 1. 在左侧会话列表里点中目标会话
session_item.click()

# 2. 等输入框出现（拼多多只有选了会话才显示输入框）
input_box = page.locator(INPUT_SELECTOR)
input_box.wait_for(state="visible", timeout=5000)

# 3. 输入文字（用 type 模拟人类敲键，不要 fill 直接刷上去）
input_box.click()
input_box.type(reply_text, delay=random.randint(40, 90))

# 4. 短随机停顿后点发送
page.wait_for_timeout(random.randint(800, 2200))
page.locator(SEND_BUTTON_SELECTOR).click()
```

**关键 selector 占位（待 `dom_probe_latest_*.json` 抓出来填）：**

| 含义 | 占位变量 | 数据来源 |
|---|---|---|
| 输入框 | `INPUT_SELECTOR` | dom_probe → "输入框候选"，挑可见且 `path` 在中下部的 |
| 发送按钮 | `SEND_BUTTON_SELECTOR` | dom_probe → "发送按钮(文字=发送)" 优先 |
| 会话列表项 | `SESSION_ITEM_SELECTOR` | dom_probe → "会话列表" |

---

## 2. 单条消息字段说明（来自 `/plateau/chat/list`）

### 2.1 通用字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `msg_id` | 消息全局唯一 ID（字符串数字） | **递增**，比对大小可判断"新消息" |
| `pre_msg_id` | 上一条的 msg_id | 链表关系，可校验消息漏抓 |
| `client_msg_id` | 客户端生成的 ID | 发送消息时本地生成 UUID |
| `ts` | 秒级时间戳 | |
| `from.role` | 发送方角色 | `"user"` 客户、`"mall_cs"` 商家客服 |
| `from.uid` | 发送方 UID | |
| `to.role` / `to.uid` | 接收方 | |
| `content` | 消息文本 / 图片 URL / 占位符 | 视 `type` 而定 |
| `type` | 消息类型，见下表 | |
| `status` | `unread` / `read` | 客户消息是否已被我方读过 |
| `is_read` | 0 / 1 | 多用于我方发出的消息（客户读了没） |
| `is_aut` | 是否自动消息 | 拼多多自家自动应答会有特征位 |

### 2.2 消息类型 `type` 参考

| type | 含义 | 处理建议 |
|---|---|---|
| `0` | 普通文本 | **核心目标**：自动回复就处理这种 |
| `1` | 图片（`content` 为图片 URL，含 `size.width/height`） | 暂忽略或回复"收到图片，稍后人工处理" |
| `31` | 系统模板（如机器人接管 / 暂停提示） | 忽略，但可触发日志告警 |
| `56` | 富文本 / 常见问题菜单 | 商家自家配置的 FAQ，忽略 |
| 其他 | 待补充（订单卡片、商品卡片、语音…） | 探查时再积累 |

### 2.3 判定"客户的待回复消息"

当且仅当一条消息满足全部条件时，才进入自动回复流程：

```python
def is_pending_user_msg(m: dict) -> bool:
    return (
        m.get("from", {}).get("role") == "user"
        and m.get("type") == 0
        and m.get("status") == "unread"
        and m.get("content")
    )
```

---

## 3. 监听新消息的策略（结合上面的发现）

### 推荐做法（双保险）

1. **HTTP 拦截**：`page.on("response")` 监听 `/plateau/chat/list` 等接口
   - 命中后解析 `result.messages`
   - 维护 `last_seen_msg_id`（每个会话独立），只处理 `msg_id > last_seen_msg_id` 的客户消息
2. **WebSocket 拦截**（兜底实时性）
   - 拼多多端到端推送多走 WS，HTTP `list` 多用于会话切换 / 回放
   - WS 协议未确认，等 `chat_*.jsonl` 抓出来分析

### 几个值得验证的问题

| 问题 | 验证方式 |
|---|---|
| 切换会话是否每次都重新拉 `chat/list` ？ | 探查脚本里多切几次会话观察 |
| 新消息进来时是 WS 推送 还是会重新拉一次 list？ | 等真实客户消息进来时观察 |
| `list` 接口是否有"按 msg_id 之后"的增量参数？ | 看请求 `post_data` / query string |
| ~~发送消息接口路径 / 请求体？~~ | 已决定走 DOM 模拟点击，不再关心 |

---

## 4. 字段速查（来自实采样本）

```jsonc
// 客户文本消息样本（type=0）
{
  "from": {"role": "user", "uid": "1444536295888"},
  "to":   {"role": "mall_cs", "uid": "116168483", "mall_id": "116168483"},
  "type": 0,
  "content": "104396161488",
  "status": "unread",
  "ts": "1777719392",
  "msg_id": "1777719392483",
  "client_msg_id": "f937e9ad-...",
  "version": 1
}

// 客户图片消息样本（type=1）
{
  "from": {"role": "user", "uid": "1444536295888"},
  "type": 1,
  "content": "https://chat-img.pddugc.com/.../xxx.jpeg",
  "size": {"width": 1080, "height": 2362, "image_size": 269},
  "status": "unread",
  ...
}

// 我方客服文本消息样本
{
  "from": {
    "role": "mall_cs",
    "uid": "116168483", "mall_id": "116168483",
    "csid": "主账号",
    "cs_uid": "XO4MXQBVBFL6B7KDUQDOCZGPRE_GEXDA"
  },
  "type": 0,
  "content": "你好",
  "status": "read",
  "is_read": 1,
  "manual_reply": 1,
  "cs_type": 2,
  ...
}
```

---

## 5. 订单字段说明（来自 `/latitude/order/userAllOrder`）

### 5.1 关键字段

| 字段 | 含义 | 备注 |
|---|---|---|
| `id` | 订单内部 ID | 字符串数字 |
| `orderSn` | 订单号 | 给客户看的格式：`260502-244821562980464` |
| `uid` | 客户 UID | 跟聊天里的 `from.uid` 对得上，**用来确认订单属于当前会话客户** |
| `mallId` | 店铺 ID | |
| `orderStatus` | 订单状态码 | 见 §5.2 |
| `payStatus` | 支付状态码 | 见 §5.2 |
| `shippingStatus` | 物流状态码 | 见 §5.2 |
| `groupStatus` | 拼团状态 | 1=已成团 |
| `orderStatusStr` | **状态文本**（已签收 / 待发货 / 已发货 / 待付款 / 已关闭…） | **首选用它**，避免猜数字状态 |
| `orderTime` | 下单时间（秒） | |
| `payTime` | 支付时间 | |
| `shippingTime` | 发货时间 | |
| `receiveTime` | 签收时间 | |
| `orderAmount` | 订单总金额 | **单位是分**（350 = 3.5 元）→ 需 `/100` |
| `goodsAmount` | 商品金额 | 同上 |
| `goodsType` | 商品类型 | `19` 多见于虚拟商品 |
| `shippingId` | 物流公司 ID | `999` 通常代表虚拟发货 / 无物流 |
| `trackingNumber` | 物流单号 | 虚拟商品为空 |
| `orderGoodsList` | 商品信息（**注意是单对象，不是数组**） | 含 `goodsId/skuId/goodsName/spec/goodsPrice/goodsNumber/thumbUrl` |
| `note` | 订单备注 | |
| `afterSalesInfo` | 售后信息 | null=无售后 |
| `compensateInfo` / `compensate` | 退货包运费 / 补偿信息 | |

### 5.2 状态码语义（经验值，待验证）

> 拼多多官方没公开文档，下面是常见取值，**实际接入时建议优先用 `orderStatusStr` 文本判断**。

| 字段 | 值 | 含义 |
|---|---|---|
| `payStatus` | 1 | 待支付 |
| | 2 | 已支付 |
| | 其他 | 退款 / 取消 |
| `shippingStatus` | 1 | 待发货 |
| | 2 | 已发货 |
| | 3 | 已签收（与 `orderStatusStr=已签收` 对应） |
| `orderStatus` | 1 | 进行中（含已签收前各阶段） |
| | 其他 | 已关闭 / 已退款 等 |

### 5.3 给 AI 用的"订单上下文"提取函数

构建 prompt 时只塞这些精炼字段，不要把整个 JSON 都丢给模型，省 token 又准：

```python
def order_summary(order: dict) -> dict:
    g = order.get("orderGoodsList") or {}
    return {
        "订单号": order.get("orderSn"),
        "状态": order.get("orderStatusStr"),
        "商品": g.get("goodsName"),
        "规格": g.get("spec"),
        "数量": g.get("goodsNumber"),
        "金额": (order.get("orderAmount") or 0) / 100,
        "下单时间": order.get("orderTime"),
        "签收时间": order.get("receiveTime"),
        "备注": order.get("note") or "",
        "有售后": order.get("afterSalesInfo") is not None,
    }
```

### 5.4 客户上下文获取策略（待落地）

聊天列表里每条会话对应一个 `user.uid`，自动回复想拿订单上下文有两条路：

| 策略 | 实现 | 优劣 |
|---|---|---|
| **被动**：拦截 `userAllOrder` 响应 | `page.on("response")` 命中时落到 `state[uid] = orders` | 不主动调接口，零额外风险，但 **只有用户点过"个人订单" tab 才会有数据** |
| **主动**：用同一登录态重放请求 | 拿到 `chat/list` 里的客户 uid 后，用 `page.request.post(...)` 调一次 `userAllOrder` | 任何会话都能立即拿到上下文，但要注意频次（单次自动回复只调一次） |

> 推荐：**初版用被动**——首次进入聊天页时引导一次"个人订单" tab 点击，把全店最近的订单缓存下；后续 AI 回复时**主动调一次**`userAllOrder?uid=xxx`（按用户 uid 过滤）拿最新状态。

---

## 6. 自动回复智能化（基于订单上下文）

完整链路：

```
[客户新消息] (chat/list)
        │
        ▼
[查 uid 对应的订单列表] (userAllOrder)
        │
        ▼
[构建 prompt: 客户消息 + 订单 summary + 历史几轮对话]
        │
        ▼
[AI 生成回复]
        │
        ▼
[DOM 模拟: 选会话 → 输入 → 点发送]
```

prompt 模板示例：

```text
你是【想要资料库】拼多多店铺的客服。请用简短、亲切的中文回复客户。
【店铺规则】
- 本店全自动发货，下单成功后客户应收到带卡券码的资料链接。
- 客户把卡券码发给我们后，按系统步骤即可领取资料。

【当前客户订单】
{order_context_or_"客户暂无订单"}

【历史对话（最近 5 轮）】
{chat_history}

【客户最新消息】
{latest_message}

请输出客服回复（不超过 60 字，必要时附上订单号）：
```


# 业务核心流程与模块对应

本文把「会话从哪里来」「单条会话怎么处理」「列表遍历时怎么跳过已处理」三块，映射到仓库里的**具体模块**，方便改逻辑时一眼定位。

---

## 一、会话监听（初始化 + 有新消息/列表更新）

| 职责 | 模块 | 说明 |
|------|------|------|
| 起浏览器、进聊天页、登录与 `page` 生命周期 | `runtime/browser.py` | `BrowserSession`；GUI 通过 `bot_worker` 调 `bot.run` |
| HTTP → 业务事件 | `runtime/network.py` | ``/plateau/sync/message``（内嵌 data→``chat_msg``，否则 ``sync_message`` 左栏补扫）、``chat/list``、``latest_conversations`` → ``chat_msg``；``userAllOrder``→``order_list`` |
| 左栏「未回复 / 小红点 / un-watch」扫描 | `tools/left_panel_scan.py` | 由 ``bot._after_sync_message`` 在每次 sync 后调用 |
| ``sync_message`` 后补跑主流程 | `bot.py` | ``_after_sync_message``：点会话、读库最新用户消息,``last_msg_id`` 已对齐则跳过 |
| 启动后「补一轮」会话/未读（冷启动） | `bot.py` | `_trigger_initial_refresh`：reload、点 tab，触发 `latest_conversations` 等，避免首屏请求早于监听安装 |
| 主循环消费事件 | `bot.py` | `run()`：`queue.get()` → ``chat_msg`` / ``sync_message`` / ``order_list`` 等；``sync_message`` 走 ``_after_sync_message`` |
| 持久化消息与会话状态 | `core/store.py` | `upsert_chat_message`、`conv_state`（含 `last_msg_id`） |
| 开关、白名单等运行参数 | `core/settings.py` | `bot.enabled`、`bot.whitelist_uids` 等 |

**小结**：「谁在听」= `network.py`（含 **sync/message**）+ `bot.run` 队列；sync 后再由 `left_panel_scan` + `_after_sync_message` 补扫左栏；「启动时多捞一次列表」= `_trigger_initial_refresh`；「落库」= `store.py`。

---

## 二、进入会话之后的决策与回复

顺序与 `bot._process_chat_msg` 中实现一致：

| 顺序 | 职责 | 模块 |
|------|------|------|
| 1 | 左侧点开目标 uid 会话 | `tools/session_dom.py` → `activate_session` |
| 2 | 右侧「最新订单」→「个人订单」，等 `userAllOrder` 刷新订单 | `tools/orders_fetch.py` |
| 3 | 规则状态机（是否转人工、走哪一 Stage） | `core/stage.py` → `decide` |
| 4 | 用聊天记录 + 订单等生成回复 | `llm/runner.py` → `arun_stage` |
| 5 | 非干跑时在输入框发送 | `tools/messaging.py` |
| 编排上述步骤、写 `action_log`、更新 `last_msg_id` | `bot.py` | `_process_chat_msg` |

被动订单缓存：`network.py` 命中 `userAllOrder` 仍会推 `order_list`，`bot.run` 里写入 `_LATEST_ORDERS`；主动回复前再由 `orders_fetch` 按当前会话刷新一次。

---

## 三、遍历会话时：已处理的不用再处理

| 机制 | 模块 | 说明 |
|------|------|------|
| 列表/冷启动来的消息带 `from_latest_convs` | `runtime/network.py` | `_handle_latest_convs` 派发 `chat_msg` 时打上 `from_latest_convs=True` |
| 与库里「该 uid 已处理过的最后一条 msg_id」比对 | `bot.py` | `_process_chat_msg`：`conv_state.last_msg_id == msg_id` 则直接 `return`；来自列表时会打 INFO，避免对同一条会话反复跑 LLM |
| sync 后左栏补扫跳过已跟进 | `bot.py` | ``_after_sync_message``：激活会话后取库中最新 **user** 消息的 ``msg_id``，若与 ``conv_state.last_msg_id`` 相同则不再构造 ``chat_msg`` |
| `last_msg_id` 何时前进 | `bot.py` + `core/store.py` | 本条走完决策/LLM/记 log 后 `upsert_conv_state(..., last_msg_id=msg_id)` |

**小结**：「遍历会话」在实现上等价于多次收到 `chat_msg`（含列表同步）；**跳过已处理**依赖 **`(uid, msg_id)` 与 `conv_state.last_msg_id` 一致**即不再进入后续 DOM / 订单 / LLM。

---

## 四、和 `md/architecture.md` 的关系

- `architecture.md`：状态机设计、决策表、数据流大图。
- 本文：**业务主链 ↔ 文件/函数** 的索引，偏「改代码时从哪打开」。

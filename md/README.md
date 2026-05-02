# pddbot —— 拼多多商家客服自动回复（Playwright + Python）

一个基于 Playwright 的拼多多商家工作台（`mms.pinduoduo.com/chat-merchant`）客服消息**监听 + 自动回复**机器人。

> 当前阶段：**探查**。先把页面的 WebSocket / HTTP / DOM 结构抓清楚，再决定监听新消息和自动回复用哪条路径最稳。

---

## 目录结构

```
pddbot/
├── config.py             # URL、路径、运行参数（读 .env）
├── login.py              # 首次扫码登录 → storage_state.json
├── explore.py            # 探查脚本：抓 WS / HTTP / DOM
├── catalog_admin.py      # 商品 ↔ 资料映射 管理 CLI
│
├── core/
│   └── store.py          # SQLite 状态库（会话 / 订单 / 核销码 / 操作日志）
│
├── tools/
│   └── catalog.py        # 商品映射查询
│
├── catalog/
│   └── product_map.json  # 商品 → 资料链接映射
│
├── assets/               # 静态资源（card_code_guide.png 等）
├── captures/             # 探查产物（gitignore）
├── logs/                 # 运行日志（gitignore）
│
├── .env.example          # 环境变量模板（DeepSeek key 等）
├── requirements.txt
│
└── md/
    ├── README.md         # 本文档
    ├── architecture.md   # 整体架构（状态机、tools、SQLite、风控）
    ├── protocol.md       # 拼多多接口字段速查
    └── assets.md         # 静态资源说明
```

---

## 1. 环境准备

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
python -m playwright install chromium

# 配置环境变量
cp .env.example .env
# 编辑 .env 填上 DEEPSEEK_API_KEY 等
```

---

## 2. 第一步：扫码登录

```bash
python login.py
```

- 会弹出有头浏览器，自动跳到拼多多商家登录页。
- 你**手动扫码**，进入到能看到聊天会话列表的【多多客服】页面。
- 回到终端，按【回车】，脚本会把 cookies / localStorage 存到 `storage_state.json`。

之后所有脚本启动时都会复用这个登录态，不需要每次重新扫码。

> ⚠️ `storage_state.json` 包含登录凭证，已加入 `.gitignore`，**不要**提交到任何代码仓库。

---

## 3. 第二步：探查页面结构

```bash
python explore.py
```

启动后做这几件事，**全程保持浏览器窗口在前台**：

1. 打开聊天页，加载完成。
2. 点开任意一个未读会话，让消息历史可见。
3. **等真实客户发来 1~2 条新消息**（最重要，用来观察 WS 推送的格式）。
4. 自己也可以正常手动回复一条，方便观察"发消息" 的请求格式。
5. 完成后按 `Ctrl+C` 结束抓取。

抓取产物位于 `captures/` 目录，文件名带本次时间戳：

| 文件 | 内容 |
|---|---|
| `chat_*.jsonl` | ★ **聊天相关接口**（`/plateau/chat/*` 等）请求和响应**完整保留** |
| `dom_probe_latest_*.json` | ★ DOM 探针，每 5 秒覆盖一次，**最终是用户操作完毕的状态** |
| `dom_probe_*.json` | DOM 探针的初始版本（页面刚打开时） |
| `ws_*.jsonl` | 所有 WebSocket 帧（JSONL，每行一条） |
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

聊天页探查完后，**单独再跑一次核销页探查**（拿核销页的 selector 和"开始核销"那一刻的 API）：

```bash
python explore_redeem.py
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

下单后客户拿到卡券码核销，核销成功后我们要给他对应商品的资料链接。这套映射用 `catalog_admin.py` 管理：

```bash
# 看现有映射
python catalog_admin.py list

# 测试一下查询逻辑
python catalog_admin.py lookup --sku-id 1876675563671
python catalog_admin.py lookup --goods-name "【系统教学】散打基础教程"

# 新增 sku 级映射（最准）
python catalog_admin.py set-sku 1876675563671 \
    --title "散打教程" \
    --url "https://pan.baidu.com/s/xxx" \
    --pwd "abcd"

# 新增关键字映射（兜底）
python catalog_admin.py add-keyword \
    --match "单片机,S010,STM32" \
    --title "STM32 单片机教程" \
    --url "https://pan.baidu.com/s/xxx" \
    --pwd "3ua3"

# 从 explore.py 抓的 chat_*.jsonl 里扫所有出现过的商品（辅助批量录入）
python catalog_admin.py scan-chat captures/chat_xxxxxx.jsonl
```

数据存在 `catalog/product_map.json`，结构和查询优先级见 [`md/architecture.md` §6](./architecture.md)。

把这 4 个文件发给我，我就能定下来：
- 监听新消息走 WS 拦截还是 DOM 监听
- 哪个 selector 是输入框、哪个是发送按钮
- 是否需要绕过额外风控

---

## 4. 路线图（探查之后）

下一步会按以下顺序补充：

1. **`listener.py`** —— 长驻监听新消息（WS 优先 + DOM 兜底）
2. **`reply/`** 模块
   - `reply/ai.py` —— 接 AI（OpenAI / DeepSeek 等）生成回复
   - `reply/keyword.py` —— 关键字快捷回复（兜底）
3. **`bot.py`** —— 主程序：监听 → 判断 → 调用 reply → 发送
4. **`risk_control.py`** —— 随机延迟、频次限制、夜间静默等防风控策略
5. **AI 配置** —— 用 `.env` 管理 `OPENAI_API_KEY` 等密钥

---

## 5. 已知风险与注意事项

- **拼多多商家后台风控较严**，长时间高频自动回复存在封号风险，正式上线前请：
  - 加入 1~3 秒随机延迟模拟真人。
  - 限制单分钟 / 单小时回复条数。
  - 设置"AI 不确定就转人工"的兜底。
- 页面 DOM / WebSocket 协议可能随拼多多前端版本变动，需要定期重新跑 `explore.py` 校准。
- 仅供 **自有店铺自用**，请遵守平台规则与相关法律法规。

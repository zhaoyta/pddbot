## Context

当前 `catalog_item` 仅存 `share_body`（整段百度网盘话术）。`tools.catalog.lookup` 与 `CatalogItem` 已从正文正则抽取 `share_url`，并用首行非空作为 `title`。模板模式（`runner._build_template_vars`）会把 `title`/`share_url`/`material_message` 交给模板；而 **Agent 模式**下 `_build_user_message` 未拼接命中映射，模型主要在 `S4_DELIVER` 通过 `lookup_product_url` 工具按需查询。咨询阶段（如 `S1_CONSULT`）若尚无订单或仅有关键词意图，缺少结构化「链接 + 简短描述」会降低推荐质量与一致性。

## Goals / Non-Goals

**Goals:**

- 在资料映射中为每条记录提供稳定、可编辑的「商品链接」与「描述」（可与 `share_body` 并存，便于 LLM 与用户展示）。
- 在与现有逻辑一致的命中优先级（goods_id → sku_id → keyword 最长命中）下，将命中结果注入 LLM 用户消息，使咨询与发货阶段均能利用同一套资料。
- 在提示词层面约束：当用户询问资料、下载方式或需要推荐时，模型结合注入内容向用户给出链接与说明。

**Non-Goals:**

- 不改变 Stage 状态机与订单驱动的路由策略（仍由 `core/stage.py` 决定）。
- 不引入外部商品 CMS 或实时抓取商品详情页 HTML（除非后续单独变更）。
- 不强制替换 `share_body`：客户侧最终可复制话术仍以整段分享文案为准。

## Decisions

1. **数据模型：为 `catalog_item` 增加可选列 `product_url`、`description`（名称可与实现统一为 `product_link` / `product_desc`，以代码为准）**  
   - **理由**：与嵌在 `share_body` 内的链接解耦，描述可短于整段话术，便于 token 控制与表格展示。  
   - **备选**：仅用解析——不新增列，运行时从 `share_body` 解析链接与首行标题。**未采纳原因**：编辑意图不明确（同一正文多种解析）、迁移后难以人工纠错。

2. **默认值与迁移**：新增列默认空字符串；启动时对已有行执行一次性回填：`product_url` 优先用现有 `CatalogItem.share_url` 解析逻辑；`description` 可用当前 `title` 或首行摘要，避免空白行无法保存的旧数据问题。  
   - **备选**：要求用户手动补全。**未采纳**：中断升级体验。

3. **注入位置**：在 `llm/runner._build_user_message`（或抽取的纯函数）中，当能从 `context["order"]` 提取 `goodsId`/`skuId`/`goodsName` 时调用 `catalog.lookup`，追加一节例如 `【商品资料映射】`（具体标题以实现为准），包含链接、描述、`share_body` 截断（设上限如 1500 字）以防超长。无订单时若有 `extra` 中携带的 goods 线索（若现有链路没有则保持不注入，避免误命中）。  
   - **理由**：与模板变量同源数据，减少工具调用往返；咨询阶段有订单时能立即感知资料。  
   - **备选**：仅扩展 system prompt 要求必须调用 `lookup_product_url`。**未采纳**：咨询阶段未必暴露该工具（当前 `make_stage_tools` 仅在 `S4_DELIVER` 提供 lookup）。

4. **推荐行为**：在对应 stage 的 system 片段（`llm/prompts.py`）增加简短规则：若上轮用户消息涉及「资料、下载、教程、推荐」且本节映射非空，应在回复中明确给出链接与一句描述，并可通过 `send_text` 发送；避免杜撰未注入的链接。  
   - **理由**：与注入上下文闭环，便于验收。

5. **GUI**：在 `CatalogEditDialog` 增加两行：`product_url`、`description`（标签可为「商品链接」「描述」），表格增加列；保存走 `upsert_catalog_item` 扩展参数。

## Risks / Trade-offs

- **[Risk] 用户消息变长、token 上升** → **Mitigation**：描述短字段 + `share_body` 严格截断；无命中则不追加块。  
- **[Risk] 链接与 `share_body` 内链接不一致** → **Mitigation**：保存时可校验 `product_url` 是否为空或与正文抽取一致（可选警告，不强阻塞）。  
- **[Risk] 无订单时无法命中 goods_id** → **Mitigation**：保留 keyword 匹配与后续对话工具；本变更不宣称覆盖无商品线索场景。

## Migration Plan

1. 部署新版本 → SQLite `ALTER TABLE` 增加列 → 迁移脚本回填解析字段。  
2. 回滚：保留列不影响旧版读取时可降级代码；若需删列则备份 DB 后手动处理（低频）。

## Open Questions

- 是否在 `S1_CONSULT` 显式开放只读的 `lookup_product_url`（或专用工具）以便无订单但用户提到商品名时查询——可与产品后续迭代讨论；本设计以「订单上下文注入」为主路径。

## Why

咨询阶段（尤其未绑定订单或仅靠对话推断商品时）模型若没有结构化「商品链接与描述」上下文，只能依赖工具调用或泛泛回答；同时后台已将资料收敛为 `catalog_item.share_body`，但链接与说明嵌在同一段正文里，不利于向模型明确投喂「可推荐的链接」与「简短描述」。需要在商品维度补齐可机读的链接与描述（或与现有 `share_body` 协同），并在 LLM 组装用户消息时注入命中规则下的资料，使涉及推荐场景时能直接产出可发送给客户的链接与话术。

## What Changes

- 在商品资料映射层增加或固化「商品链接」「描述」的可编辑字段（可与现有整段 `share_body` 并存：描述用于模型摘要，链接用于明确输出；或约定从 `share_body` 解析并回填展示，迁移旧数据）。
- 在 `LLM` 路径（`runner._build_user_message` 或等价位置）按当前会话订单 / 上下文中解析出的 `goods_id`、`sku_id`、`goods_name` 执行与 `tools.catalog.lookup` 一致的命中逻辑，将命中结果的链接、描述及必要时整段 `share_body` 摘要注入模型输入。
- 更新阶段提示词或工具说明：当用户意图涉及资料获取、下载、推荐同类资料时，模型应结合注入的链接与描述向用户推荐，并通过已有 `send_text` / `lookup_product_url` 等路径发送给客户（避免重复或冲突的行为需在实现中约定）。
- GUI「商品 ↔ 资料映射」页支持编辑上述字段并与 SQLite `catalog_item` 同步（若新增列则含迁移）。
- **BREAKING**：若有对外 JSON/API 依赖 `catalog_item` 仅含三列的旧假设，需在迁移说明中列出（本仓库主要为本地 SQLite + GUI）。

## Capabilities

### New Capabilities

- `product-catalog-llm-context`：涵盖商品资料（链接、描述、与 `share_body` 的关系）、命中规则不变（goods_id → sku_id → keyword 最长命中）、LLM 用户消息注入规则，以及「涉及推荐时向用户给出链接与描述」的行为要求。

### Modified Capabilities

- （无：`openspec/specs/` 下尚无基线 spec，本次全部为新增能力。）

## Impact

- **数据**：`core/store.py` 中 `catalog_item` 表结构及迁移、`upsert`/`list`/`find_catalog` 等。
- **GUI**：`gui/pages/catalog.py`（表单列、校验、表格列）。
- **工具与 Runner**：`tools/catalog.py`（`CatalogItem` 字段与序列化）、`llm/runner.py`（上下文拼装）、`llm/prompts.py` / `llm/agent.py`（如有阶段绑定的 system prompt）。
- **LangChain 工具**：`llm/tools.py` 中 `lookup_product_url` 与注入上下文的一致性，避免重复矛盾。
- **文档**：可选更新 `md/architecture.md` 中商品映射与 LLM 上下文章节（实现阶段跟进）。

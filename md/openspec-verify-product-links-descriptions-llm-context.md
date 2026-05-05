# Verification Report: product-links-descriptions-llm-context

## Summary

| Dimension    | Status |
|--------------|--------|
| Completeness | 11/11 tasks checked complete |
| Correctness  | 核心数据层与 lookup/GUI 符合最初规格；**LLM 注入策略已与 delta spec 文稿不一致**（见 WARNING） |
| Coherence    | 实现与 **design.md 决策 2～3** 部分偏离（有意迭代）；归档前应同步更新 spec/design/tasks |

---

## 1. Completeness（任务清单）

来源：`openspec/changes/product-links-descriptions-llm-context/tasks.md`

| 分组 | 任务 | 状态 |
|------|------|------|
| 1 数据层 | 1.1 / 1.2 | `[x]` |
| 2 工具 | 2.1 / 2.2 | `[x]` |
| 3 LLM | 3.1 / 3.2 / 3.3 | `[x]` |
| 4 GUI | 4.1 / 4.2 | `[x]` |
| 5 测试与文档 | 5.1 / 5.2 | `[x]` |

**结论**：清单层面无未勾选项。

**说明**：任务 **3.1** 原文仍写「订单商品字段…追加映射…share_body 截断」，与当前实现（全店索引、无 share_body）不符——属于 **制品未随代码迭代**，而非未完成开发。

---

## 2. Correctness（规格对照）

Delta spec：`openspec/changes/product-links-descriptions-llm-context/specs/product-catalog-llm-context/spec.md`

### Requirement: Catalog stores explicit product link and description

- **证据**：`core/store.py` 中 `catalog_item` 含 `product_url`、`description`；`upsert_catalog_item` 支持读写。
- **Scenario: Legacy rows**：规格要求空字段可从 `share_body` 回填链接与/或描述。
- **实现**：`_migrate_catalog_product_columns` **仅回填空的 `product_url`**（从正文解析网盘链接），**刻意不回填 `description`**（见 `store.py` 迁移注释），以免咨询场景把正文摘要写入描述列。
- **判定**：与 delta spec 字面 **部分不一致** → **WARNING**：行为合理，但应在归档前 **修订 spec** 或注明「描述仅人工维护」。

### Requirement: Catalog lookup exposes link and description

- **证据**：`tools/catalog.py` 中 `CatalogItem.product_url`、`description`（含显式优先与解析回退）；`lookup` / `all_items` 暴露列；`to_dict()` 与 `llm/tools.py` 中 `lookup_product_url` 一致。
- **判定**：**符合**。

### Requirement: LLM user message includes mapped material when order goods context exists

- **规格要点**：仅当存在订单商品上下文且 `catalog.lookup` 命中时注入；且含 **truncated full `share_body`**。
- **当前实现**：`llm/runner.py` 使用 `_catalog_shop_index_block()` → **全店** `catalog_item` 列表（`match_type`/`match_value` + 链接 + **仅后台 `description` 列**），**不注入 `share_body`**，**不依赖订单命中单条**。
- **判定**：与 delta spec **显著偏离** → **WARNING（归档前必处理）**：要么更新 **MODIFIED Requirements** / 新 change 文档化「全店索引 + 咨询不卖全文」，要么把实现改回订单命中 + share_body 截断（与当前产品意图相反）。

### Requirement: Recommendation replies use injected material

- **证据**：`llm/prompts.py` 中 `COMMON_RULES` 第 12～13 条、`S1_CONSULT_PROMPT` 对「店铺商品资料索引」的说明。
- **判定**：**意图符合**（基于索引与工具，禁止捏造链接）。索引区块标题已从「映射段落」改为「全店索引」，与实现一致。

---

## 3. Coherence（设计文档）

来源：`openspec/changes/product-links-descriptions-llm-context/design.md`

| 决策摘要 | 设计意图 | 当前实现 |
|----------|----------|----------|
| 迁移回填描述（首行/title） | 旧库升级体验 | **未回填 description**，仅 `product_url` |
| 注入位置：订单 `orderGoodsList` + lookup + **share_body 截断** | 有订单即注入 | **全店索引**，无订单亦可咨询；**零 share_body** |

**判定**：属 **有意产品设计迭代**，非代码遗漏；归档 OpenSpec 前应 **更新 design.md / proposal / delta spec**，避免后续核验误判。

---

## 4. Issues by Priority

### WARNING（建议归档前处理）

1. **Delta spec 与实现对 LLM 注入的描述不一致**（订单命中 + share_body 截断 vs 全店索引 + 无 share_body）。  
   **建议**：对 `specs/product-catalog-llm-context/spec.md` 做 **MODIFIED/ADDED** 修订，或新建 change 记录「咨询索引」迭代。

2. **Legacy 描述回填**：spec 写「link and/or description from parsed」；实现仅回填链接。  
   **建议**：在 spec 中明确「description 仅人工填写」或恢复可选回填（需产品确认）。

3. **tasks.md 3.1 文案过时**。  
   **建议**：将 3.1 改为与 `_catalog_shop_index_block` 一致，或指向 architecture 小节。

### SUGGESTION

1. **场景测试**：delta spec 中「无订单是否注入」已由全店索引覆盖；可在 `_smoke_test` 或注释中写明「订单唯一命中」已不再作为注入前提，便于回归。

2. **关联变更**（本次核验范围外）：`bot.py` 合并 `send_text`、`messaging.py` 换行规范化、`sync` 去重等与「商品资料 LLM」变更独立，无需写入本 change 的 tasks，但若归档叙事需要可在 proposal 中脚注「后续 UX 修复」。

---

## 5. Final Assessment

- **CRITICAL**：无（任务已全部勾选，核心存储与 lookup/GUI 已实现）。
- **WARNING**：共 **3** 条，均与 **制品相对代码滞后** 有关，不影响「能否运行」，但 **不建议在未更新 spec/design 的情况下直接 archive**。

**结论**：**No critical issues.** 存在若干 **WARNING**：建议在归档前 **同步 OpenSpec 制品与当前行为**，然后执行 archive。**Ready for archive（在完成制品修订后）。**

---

## 6. 关键代码索引（便于复查）

| 能力 | 位置 |
|------|------|
| 表结构与迁移 | `core/store.py`（`catalog_item`、`_migrate_catalog_product_columns`） |
| CatalogItem / lookup / all_items | `tools/catalog.py` |
| 全店 LLM 索引 | `llm/runner.py`：` _catalog_shop_index_block`、` _build_user_message` |
| 模板占位符 `product_url` / `description` | `llm/runner.py`：`TEMPLATE_PLACEHOLDERS`、`_build_template_vars` |
| 工具返回 | `llm/tools.py`：`lookup_product_url` → `item.to_dict()` |
| GUI | `gui/pages/catalog.py` |
| 架构说明 | `md/architecture.md` §6 / §9 |

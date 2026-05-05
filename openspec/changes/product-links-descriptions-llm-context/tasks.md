## 1. 数据层与迁移

- [x] 1.1 在 `catalog_item` 上新增 `product_url` / `description`，`_migrate_*`：`ALTER TABLE`；旧行仅对空的 `product_url` 从正文解析网盘链接回填（`description` 不自动从正文推断）
- [x] 1.2 扩展 `Store.upsert_catalog_item`、`list_catalog_items`、`find_catalog` 等读写路径，保证唯一约束与校验（`share_body` 仍必填；新列可空或有默认）

## 2. 工具与模型对象

- [x] 2.1 更新 `tools/catalog.py` 中 `CatalogItem`：增加 `product_url`、`description` 属性；`lookup`/`all_items` 返回字典时带上新字段；显式字段优先，否则回退到现有 `share_url`/首行 `title` 解析逻辑
- [x] 2.2 同步 `llm/tools.py` 中 `lookup_product_url` 的返回结构说明与实际字段，与 `CatalogItem.to_dict()` 一致

## 3. LLM 上下文与提示词

- [x] 3.1 在 `llm/runner._build_user_message` 中注入「店铺商品资料索引」：列出全店 `catalog_item` 的匹配键、链接与后台「描述」列；不向模型注入 `share_body` 全文（见 delta spec）
- [x] 3.2 在 `llm/prompts.py`（及相关 stage 文案）补充规则：涉及资料/下载/推荐时基于注入内容作答，禁止捏造链接；必要时引导使用 `send_text`
- [x] 3.3 回归模板模式：`TEMPLATE_PLACEHOLDERS` / `_build_template_vars` 如需新增占位符则更新 `gui/pages/stage_reply.py` 提示或默认值

## 4. GUI

- [x] 4.1 更新 `gui/pages/catalog.py`：编辑对话框与表格列展示「商品链接」「描述」；保存调用扩展后的 `upsert_catalog_item`
- [x] 4.2 手动验证：新增/编辑/删除、`命中测试` 与列表导出逻辑仍正常（自动化：`python -m llm._smoke_test` 覆盖 lookup/runner；GUI 需在本地启动验证）

## 5. 测试与文档

- [x] 5.1 扩展或新增 `llm/_smoke_test.py` / 单测：迁移后行可读、lookup 字段优先级、runner 组装消息含映射块（可用最小伪造 `context`）
- [x] 5.2 在实现完成后按需更新 `md/architecture.md` 中商品映射与 LLM 上下文的小节（若本次引入新占位符或表结构）

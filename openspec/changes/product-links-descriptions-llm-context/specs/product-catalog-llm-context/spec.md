## MODIFIED Requirements

### Requirement: Catalog stores explicit product link and description

The system SHALL persist, per `catalog_item` row, explicit `product_url` and `description` columns in addition to `share_body`, with uniqueness and match behavior unchanged (`match_type` + `match_value`).

#### Scenario: Editor saves link and description

- **WHEN** an operator saves a catalog row with non-empty `share_body` and optional `product_url` / `description`
- **THEN** the system SHALL store all fields and return the row on subsequent read

#### Scenario: Legacy migration backfills product URL only

- **WHEN** the database is upgraded from rows that only had `share_body`
- **THEN** the system SHALL add empty `product_url` / `description` columns as needed and MAY populate **only** empty `product_url` by parsing a Baidu Pan–style URL from `share_body`
- **THEN** the system SHALL NOT auto-fill `description` from `share_body` text (description is operator-maintained for LLM-facing short copy; avoids injecting核销话术摘要 into the description column)

### Requirement: Catalog lookup exposes link and description to callers

The catalog lookup API (`tools.catalog.lookup` / `CatalogItem`) SHALL expose effective product link and description for a successful hit: explicit stored fields SHALL take precedence over values derivable from `share_body` (e.g. parsed pan URL, first-line title for template/runtime `description` where applicable).

#### Scenario: Hit prefers explicit stored fields for URL

- **WHEN** a lookup hits a row with both explicit `product_url` and parseable links inside `share_body`
- **THEN** the effective outbound link (`product_url` property / `to_dict`) SHALL use the explicit stored URL when non-empty

### Requirement: LLM agent user message includes shop-wide catalog index

When building the LLM user message for agent mode, the system SHALL include a dedicated section listing **all** `catalog_item` rows (sorted by `match_type` / `match_value`), each line showing match key, effective product link, and **only** the stored `description` column text (or a placeholder when empty). The system SHALL NOT inject full `share_body` into this section (核销专用全文仅在工具 `lookup_product_url` / `send_text` 流程中发给客户).

#### Scenario: Agent message contains index when catalog non-empty

- **WHEN** at least one catalog row exists and the agent path runs
- **THEN** the packed user message SHALL contain a `【店铺商品资料索引】` (or equivalent) block with every row’s link and description fields as specified, subject to a configurable maximum character budget for the index block

#### Scenario: Empty catalog omits index block

- **WHEN** no catalog rows exist
- **THEN** the system MAY omit the index section

#### Scenario: Consultation without current order still has index

- **WHEN** the customer asks whether materials exist for a topic (e.g. keyword / theme) and there is **no** order in context
- **THEN** the model SHALL still receive the shop-wide index when rows exist, so it can answer from `match_value` / description without relying on `goods_id` lookup alone

### Requirement: Recommendation replies use injected material

When the user’s message implies recommendation, resource delivery, or download intent regarding course/material products, the assistant SHALL base recommendations on the **shop-wide index**, tools output (`lookup_product_url`), and order facts; it SHALL NOT invent URLs absent from those sources.

#### Scenario: User asks for materials by topic

- **WHEN** the user asks whether certain materials exist and the index or tools provide links
- **THEN** the assistant’s reply SHALL use those links (or clearly state unavailability) and SHALL NOT fabricate URLs not present in the index or tool results

## REMOVED Requirements

### Requirement: LLM user message includes mapped material when order goods context exists

**Reason**: Product decision replaced order-scoped single-hit injection (with optional `share_body` truncation) with a **shop-wide index** of links and operator descriptions only, so consult can answer “有没有某类资料” without a current order and without leaking full `share_body` to the LLM.

**Migration**: Read `llm/runner.py` (`_catalog_shop_index_block`, `_build_user_message`) and `md/architecture.md` §6 / §9 for current behavior.

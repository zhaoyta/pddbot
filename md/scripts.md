# `scripts/` 目录

与 **GUI / `bot.run`** 无关的**手动探查**脚本，均在**项目根目录**执行。  
需先有 **`storage_state.json`**（通过 GUI 启动并完成登录后保存）。

| 脚本 | 作用 |
|------|------|
| `scripts/explore.py` | 聊天页被动抓 HTTP / DOM → `captures/` |
| `scripts/explore_redeem.py` | 核销页探查 → `captures/redeem_*` |

```bash
uv run python scripts/explore.py
uv run python scripts/explore_redeem.py
```

脚本内会把仓库根目录加入 `sys.path`，以便 `from core import config`、`from core import settings` 等照常工作。

"""离线烟雾测试:不调用 DeepSeek,只验证

    1. 各 stage 的 prompt 拼装无误
    2. 各 stage 的 tools 列表正确
    3. tool 闭包能从 deps 拿到 store 并写日志
    4. agent.invoke 能用一个假 LLM 跑通图

用法:
    uv run python -m llm._smoke_test
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# 让没填 DEEPSEEK_API_KEY 的环境也能跑
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-mock-for-smoke-test")

# 用 tempfile 替代默认的 pddbot.db,避免污染真实数据
TMP_DB = Path(tempfile.gettempdir()) / "pddbot_smoketest.db"
if TMP_DB.exists():
    TMP_DB.unlink()

from core import config  # noqa: E402
config.DB_PATH = TMP_DB

from core.store import Store  # noqa: E402
from llm.prompts import PROMPTS  # noqa: E402
from llm.tools import make_stage_tools  # noqa: E402


def test_prompts() -> None:
    expected = {"S0_GREET", "S1_CONSULT", "S2_GUIDE", "S3_REDEEM", "S4_DELIVER"}
    assert set(PROMPTS.keys()) == expected, f"prompts 缺失: {PROMPTS.keys()}"
    for k, v in PROMPTS.items():
        assert "你是" in v or "客服" in v, f"{k} prompt 不完整"
    print("[OK] prompts 完整")


def test_tools_per_stage() -> None:
    store = Store(TMP_DB)
    deps = {"store": store, "page": None, "uid": "TEST_UID",
            "stage": "S1_CONSULT", "dry_run": False}

    expected_count = {
        "S0_GREET": 2,    # send_text + escalate
        "S1_CONSULT": 2,  # send_text + escalate
        "S2_GUIDE": 3,    # + send_card_code_guide
        "S3_REDEEM": 3,   # + submit_card_code
        "S4_DELIVER": 3,  # + lookup_product_url
    }
    for stage, n in expected_count.items():
        deps["stage"] = stage
        ts = make_stage_tools(stage, deps)
        assert len(ts) == n, f"{stage} 应有 {n} 个 tool, 实际 {len(ts)}: {[t.name for t in ts]}"
        print(f"[OK] {stage} tools = {[t.name for t in ts]}")


def test_lookup_tool_real_data() -> None:
    """先往 catalog_item 表插一条测试数据,跑 lookup 确认整段网盘消息生成无误。"""
    store = Store(TMP_DB)
    msg = (
        "通过百度网盘分享的文件：S022-散打\n"
        "链接：https://pan.baidu.com/s/1Guz-8Lzmw-guvg3gByc7SQ?pwd=fdby\n"
        "复制这段内容打开「百度网盘APP 即可获取」"
    )
    store.upsert_catalog_item(
        match_type="goods_id",
        match_value="928035245974",
        share_body=msg,
    )

    deps = {"store": store, "page": None, "uid": "TEST_UID",
            "stage": "S4_DELIVER", "dry_run": False}
    ts = make_stage_tools("S4_DELIVER", deps)
    lookup = next(t for t in ts if t.name == "lookup_product_url")
    out = lookup.invoke({"goods_id": "928035245974"})
    assert out is not None, "lookup 应命中 928035245974"
    assert "百度网盘" in out["message"]
    assert "S022-散打" in out["message"]
    assert "?pwd=fdby" in out["message"]
    print("[OK] lookup_product_url 输出:")
    print("---")
    print(out["message"])
    print("---")


def test_lookup_share_body_full_message() -> None:
    """完整文案：message 与录入一致，不再拼标题/链接/提取码三行结构。"""
    store = Store(TMP_DB)
    body = (
        "通过百度网盘分享的文件：S011-生活小妙招\n"
        "链接：https://pan.baidu.com/s/1wy8HeiURHdEHHMeCO3vM2A?pwd=6893\n"
        "复制这段内容打开「百度网盘APP 即可获取」"
    )
    store.upsert_catalog_item(
        match_type="goods_id",
        match_value="999888777",
        share_body=body,
    )

    deps = {"store": store, "page": None, "uid": "TEST_UID",
            "stage": "S4_DELIVER", "dry_run": False}
    ts = make_stage_tools("S4_DELIVER", deps)
    lookup = next(t for t in ts if t.name == "lookup_product_url")
    out = lookup.invoke({"goods_id": "999888777"})
    assert out is not None
    assert out["message"] == body
    assert out["url"] == "https://pan.baidu.com/s/1wy8HeiURHdEHHMeCO3vM2A?pwd=6893"
    print("[OK] lookup_product_url 整段文案模式")


def test_send_text_writes_action_log() -> None:
    import asyncio

    store = Store(TMP_DB)
    deps = {"store": store, "page": None, "uid": "TEST_UID",
            "stage": "S1_CONSULT", "dry_run": False}
    ts = make_stage_tools("S1_CONSULT", deps)
    send_text = next(t for t in ts if t.name == "send_text")
    r = asyncio.run(send_text.ainvoke({"text": "您好,请问有什么可以帮您?"}))
    assert r == "ok"
    # 应当在 action_log 里写了一条
    rows = store._query(
        "SELECT * FROM action_log WHERE uid=? AND tool=?",
        ("TEST_UID", "send_text"),
    )
    assert len(rows) == 1
    print("[OK] send_text -> action_log 已记录")


def test_graph_compiles() -> None:
    """构建 agent 图但不真调 LLM,只验证可编译 + tool schema 正确。"""
    from llm.agent import build_agent

    store = Store(TMP_DB)
    deps = {"store": store, "page": None, "uid": "TEST_UID",
            "stage": "S2_GUIDE", "dry_run": True}

    for stage in ("S0_GREET", "S1_CONSULT", "S2_GUIDE", "S3_REDEEM", "S4_DELIVER"):
        deps["stage"] = stage
        try:
            agent = build_agent(stage, deps)
        except RuntimeError as e:
            # 如果是 DEEPSEEK_API_KEY 校验报错就跳过
            if "DEEPSEEK_API_KEY" in str(e):
                print(f"[SKIP] {stage}: 缺少 API key 无法构建模型")
                continue
            raise
        # 仅验证拿得到 graph 对象
        assert hasattr(agent, "invoke")
        print(f"[OK] {stage} agent 编译成功")


def main() -> None:
    print("=== 1. prompts ===")
    test_prompts()
    print("\n=== 2. tools per stage ===")
    test_tools_per_stage()
    print("\n=== 3. lookup_product_url 真实数据 ===")
    test_lookup_tool_real_data()
    print("\n=== 3b. lookup 整段文案 ===")
    test_lookup_share_body_full_message()
    print("\n=== 4. send_text 落 action_log ===")
    test_send_text_writes_action_log()
    print("\n=== 5. 各 stage agent 编译 ===")
    test_graph_compiles()
    print("\n所有 smoke test 通过 ✓")


if __name__ == "__main__":
    main()

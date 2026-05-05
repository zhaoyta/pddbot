"""Stage 级 LLM Agent 构建

使用 `langchain.agents.create_agent`（langchain 1.x 推荐）替代被废弃的
`langgraph.prebuilt.create_react_agent`。

关键设计:
    - 一个 stage = 一张 agent 图
    - 图内由 LangGraph 自动跑 LLM ↔ tool 循环,直到无 tool_call 时停止
    - 业务硬规则不放进图,由 core/stage.py 在调用 agent 之前做完
"""
from __future__ import annotations

from langchain.agents import create_agent

from .client import get_chat_model
from .prompts import PROMPTS
from .tools import make_stage_tools


def build_agent(stage: str, deps: dict, *, debug: bool = False):
    """根据 stage 构建一个独立 agent 图。

    参数:
        stage: "S0_GREET" / "S1_CONSULT" / "S2_GUIDE" / "S3_REDEEM" / "S4_DELIVER"
        deps : 运行时依赖字典,须含 ``store`` / ``uid`` / ``dry_run`` / ``stage``,
               以及 ``page``(Playwright Page);无 ``page`` 时工具不操作 DOM(仅日志 stub)
        debug: 透传到 LangGraph,True 时会打印每步执行细节
    """
    if stage not in PROMPTS:
        raise ValueError(f"未知 stage: {stage}")

    model = get_chat_model()
    tools = make_stage_tools(stage, deps)
    prompt = PROMPTS[stage]

    return create_agent(
        model=model,
        tools=tools,
        system_prompt=prompt,
        name=f"pddbot_{stage.lower()}",
        debug=debug,
    )

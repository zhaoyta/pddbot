"""LLM Agent 层（基于 LangGraph 1.x / langchain.agents.create_agent）

业务硬规则在 core/stage.py 决策完后，进入这里：
    runner.run_stage(stage, context, deps) -> str  最终回复文本

每个 stage 对应一张图，仅暴露该 stage 允许调用的 tools。
"""

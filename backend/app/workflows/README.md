# 工作流入口

图片与平台参数模式已启用真实LangGraph预检与编号校验图，模型SSE仍通过现有网关传输。单文件模式保持直接执行。

详见项目docs/agent-evidence.md。单次模型调用，无自动付费重试；尚无跨请求checkpoint恢复。

Agent本身已独立到backend/app/agents/sonar_agent.py：提示词、候选配对输出契约、同步/流式模型调用集中于该文件；workflows只做流程编排，services保留算法。

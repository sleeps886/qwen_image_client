# Agent 独立模块

`sonar_agent.py`集中放置实际声呐观测/关联Agent的提示词、输出契约和同步/流式调用入口。

- `build_association_prompt(...)`：注入已有规则、候选点、亮带片段与数量线索，不改原契约。
- `SonarAssociationAgent.invoke_state/stream_state`：通过注入的ModelGateway发起原有单次图片调用，不重试，不保存密钥。
- `backend/app/workflows`继续负责LangGraph图、节点状态、日志、调用顺序与结果落盘。
- `backend/app/services`继续负责确定性图像处理、数量/身份复核、定位及绘图，它们不是大模型Agent。
- `backend/app/infrastructure/llm`继续负责HTTP协议、认证与SSE解析；单文件模式不走声呐Agent。

这次仅拆分代码职责，不新增Agent数量，不改变模型、识别算法、调用次数或费用策略，不代表识别准确度改善。

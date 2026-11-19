# Agent 模块职责

统一放在 `backend/app/agents/` 下，但不合并为巨型文件。

```text
agents/
├─ sonar_agent.py             原模型关联 Agent：任务生命周期和高层调度
├─ forward_inverse_agent.py   本地反演 Agent：预算、执行、落盘和结果汇总
├─ state.py                   四类状态契约，不保存密钥
├─ prompts.py                 候选关联提示词和模型输出契约
├─ tools.py                   单次模型关联调用工具，不自动重试
├─ progress.py                节点名称、真实开始/结束事件及耗时
├─ serialization.py           诊断结果序列化，剔除私有数组字段
├─ hypothesis_review.py       可选模型假设复核提示与严格输出校验
├─ nodes/
│  ├─ model.py                普通模型调用、流式调用及绘图适配节点
│  ├─ evidence.py             规则、候选、数量线索、配对和身份复核节点
│  ├─ tracking.py             定位、连续估计和重投影节点
│  └─ inverse.py              本地反演的九个节点动作
└─ graphs/
   ├─ evidence.py             预检与关联复核图的节点/边定义
   ├─ tracking.py             定位与跟踪图的节点/边定义
   └─ inverse.py              本地九节点图的节点/边定义
```

## 执行边界

- API 通过依赖取得 `SonarAgent`，本地反演 API 调用 `ForwardInverseAgent`。
- Agent 调用图；图连接节点；节点调用已划分的 services 和工具。
- Agent 入口不重复定义节点函数，不内嵌本地九节点实现。
- 工具负责一次模型调用；模型协议、鉴权与网络实现仍在 infrastructure/llm。
- 数值与图像算法仍在 services，不为目录改造重复包装为无实际用途的工具。
- 不恢复顶层 workflows 目录，不保留旧单文件中的代理导出。
- 前端 Agent 运行面板及事件字段保持不变，调用次数、授权和不自动重试规则保持不变。

# Service 按职责分包

此次为目录和职责重构，不修改识别算法、定位参数、接口地址和轨迹精度结论。

## 当前目录

```text
backend/app/services/
├─ inputs/                       输入解析与契约校验
│  ├─ input_service.py            图片/数据输入分流、统一上下文
│  ├─ data_input.py               JSON/JSONL/受限 JS 数据读取与点位规范化
│  ├─ meta_tracks.py              根据显式仿真参数重建轨迹
│  ├─ model_json.py               提取完整模型 JSON 对象
│  ├─ platform_input.py           平台数据校验；由原 trajectory.py 拆出
│  └─ track_validation.py         轨迹 JSON 校验；由原 trajectory.py 拆出
├─ jobs/                         任务生命周期
│  ├─ inverse_jobs.py             反演输入准备、上传校验和资源释放
│  └─ inversion_control.py        取消、截止时间和预算检查
├─ measurements/                 图像证据与测量
│  ├─ sonar_evidence.py           原流程候选点、编号校验及证据叠加
│  ├─ btr_measurements.py         声明配色解码、高斯剖面拟合和诊断预览
│  └─ btr_field.py                完整反演的密集剖面测量
├─ association/                  数量、路径和身份关联
│  ├─ target_count.py             亮带片段、数量线索与覆盖审计
│  ├─ crossing_tracks.py          局部 X 交叉连续性假设
│  ├─ identity_association.py     原模型关联的双阵身份复核
│  └─ ridge_hypotheses.py         折叠角展开、路径搜索和双阵配对提案
├─ localization/                 空间定位和轨迹反演
│  ├─ forward_observation.py      与仿真约定对应的正向观测模型
│  ├─ spatial_tracks.py           原流程的双阵射线交点和镜像候选
│  ├─ continuous_tracks.py        原流程的有界窗口估计与重投影
│  ├─ batch_inverse.py            完整反演的运动拟合、全局互斥选择
│  └─ profile_inverse.py          已选身份的原图强度剖面精修
├─ quality/
│  └─ inverse_quality.py          完整反演的 BTR 回投和残差报告
├─ rendering/
│  └─ trajectory.py               绘图与旧流程绘图入口
├─ evaluation/
│  ├─ comparison.py              图片视觉轮廓比较
│  └─ evaluation.py              结构化参考轨迹评测
└─ knowledge/
   └─ rules.py                    内置版本化规则检索，原 knowledge.py
```

所有子目录都包含轻量 `__init__.py`；不自动导入所有算法，也不创建重复模块别名。

## 依赖约定

- `api` 接收 HTTP 请求；`jobs` 管理任务；`agents/` 中的入口和图模块编排执行步骤。
- `inputs` 不依赖绘图、定位或 HTTP，读取参数不应触发绘图依赖。
- 图像测量、身份关联、定位和质量模块由完整 Agent 按阶段调用，不自行调用云端 Agent。
- `render_document()` 只绘制已准备好的轨迹文档。
- 旧 `render_tracks()` 为兼容调用入口，仍负责校验/空间求解分流后调用绘图；此行为未改变。
- 旧 `continuous_tracks.py` 的重投影方法仍与窗口估计保存在同一文件，本轮不拆改其算法实现。
- 异常分类仍在 `core/exceptions.py`；HTTP 映射仍在 `api/exception_handlers.py`。

## 导入迁移

项目内部调用方、测试中的 mock 路径、离线脚本和文档链接已同步调整。例如：

```python
from backend.app.services.inputs.input_service import prepare_input
from backend.app.services.inputs.platform_input import platform_context
from backend.app.services.inputs.track_validation import parse_tracks
from backend.app.services.jobs.inverse_jobs import prepare_inverse_job
from backend.app.services.localization.batch_inverse import fit_proposals
from backend.app.services.rendering.trajectory import render_document
from backend.app.services.knowledge.rules import retrieve_rules
```

旧扁平路径不保留代理文件；外部自编脚本若使用旧路径，需要一并更新。
不搬动 `.env`、上传临时目录、历史输出、日志或仿真工具源码。
运行中的 Python 进程仍持有旧模块，需重启后端后加载新结构。

## 验证

完整后端测试覆盖两套工作流、接口、取消清理、异常响应及轨迹校验/绘图。
架构测试改为递归扫描子包，并增加分包布局、输入层依赖和校验/绘图职责检查。
这次结构重构不代表声纳识别精度提升，也未调用真实付费模型。

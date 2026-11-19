# 声纳轨迹反演项目实现说明

版本 V1.0  
整理日期 2026年11月18日  
整理者 小怪  
项目目录 `C:\Users\LENOVO\Desktop\btr_gens\qwen_image_client`

## 1 文档目的与核心结论

本文面向项目维护者，结合现有代码说明程序如何启动、前后端如何通信、声纳图如何转换为空间轨迹，以及Agent、LangGraph和知识库目前实际承担的职责。

当前项目是一个本地运行的声纳图像测量与空间反演应用，包含原模型关联、完整本地反演和单文件绘图三条路线。当前主要优化路线是完整本地反演：程序负责测量、关联、运动求解和绘图，大模型仅在用户明确授权后进行一次受限假设复核。

项目已经形成可运行、可记录过程和可离线比较的工程链路，但尚不能声称可靠恢复所有目标、唯一解决镜像或适用于真实声纳。案例知识库仍是技术设计，当前实际使用的是内置规则库。

本文以当前代码为依据，不修改仿真工具和运行代码。文中的182项测试和约25秒浏览器运行记录来自最近一次验证，本次整理文档未重新执行全部测试。

## 2 系统组成与启动

### 2.1 技术组成

| 组成 | 当前技术与用途 |
|---|---|
| 前端 | 原生HTML、CSS、JavaScript，负责上传、选项、节点和结果展示 |
| Web服务 | FastAPI与Uvicorn，提供页面、静态资源和HTTP接口 |
| 流程编排 | LangGraph，顺序执行节点并传递请求状态 |
| 图像与数值处理 | Pillow、NumPy、SciPy，处理图像、峰值、拟合和关联 |
| 绘图 | Matplotlib，生成轨迹和诊断图 |
| 模型接入 | 网关支持OpenAI兼容格式与Anthropic Messages格式 |
| 持久化 | 本地结果文件，不是关系数据库中的业务表 |
| 规则检索 | 内置规则写入内存SQLite后按阶段和版本查询 |

依赖定义见 [backend/requirements.txt](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/requirements.txt)。根目录requirements文件是兼容入口，实际引用后端依赖。

### 2.2 启动路径

```powershell
cd "C:\Users\LENOVO\Desktop\btr_gens\qwen_image_client"
python -m backend.app.main
```

默认网页地址为 `http://127.0.0.1:8870/`，接口文档为 `http://127.0.0.1:8870/docs`。

- [main.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/main.py)中的`create_app()`创建FastAPI应用、注册中间件、路由和异常处理。
- 同文件的`main()`使用Uvicorn启动本地服务。
- [app.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/app.py)只是兼容启动入口，不是另一套业务实现。
- [router.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/api/router.py)汇总system、recognition、inversion、results、evaluation、logs接口。
- [paths.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/core/paths.py)集中定义项目、静态资源、上传、结果和日志路径。

前后端虽分目录，但当前由同一个FastAPI服务部署，浏览器访问同源API，不是两个独立部署的服务。

## 3 代码分层与职责

| 层次 | 文件示例 | 职责与边界 |
|---|---|---|
| 页面交互 | [frontend/app.js](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/frontend/app.js) | 组织请求、读取SSE、更新页面，不在浏览器求空间轨迹 |
| HTTP边界 | [inversion.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/api/routers/inversion.py) | 接收上传、校验请求、创建任务、回传事件 |
| 工作流 | [forward_inverse_agent.py · 本地反演编排](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/agents/forward_inverse_agent.py) | 编排节点和状态，调用算法服务，保存阶段报告 |
| 算法服务 | [batch_inverse.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/localization/batch_inverse.py) | 运动拟合、关联选择和轨迹文档构造 |
| Agent | [hypothesis_review.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/agents/hypothesis_review.py) | 构造受限复核上下文，验证模型输出 |
| 外部模型适配 | [gateway.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/infrastructure/llm/gateway.py) | 隔离模型协议和客户端，允许测试时替换网关 |
| 结果仓储 | [result_repository.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/repositories/result_repository.py) | 建立结果目录、白名单下载和读取历史 |

`models`和`schemas`用于内部对象与契约；目录存在不等于已经建立数据库ORM系统。`legacy`保留兼容实现，不是新反演主入口。

## 4 三条业务路线

### 4.1 原模型关联流程

```text
图片和平台参数
  → 本地候选、持续亮带、数量线索
  → 规则与图片上下文
  → 大模型返回目标清单和candidate_pairs
  → 程序校验编号、同步时间和证据独占
  → 双阵定位、连续估计、重投影
  → 绘图和保存
```

主要代码如下：

- [sonar_agent.py · 原关联编排](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/agents/sonar_agent.py)：`SonarAgent`总调度。
- [graphs/evidence.py · 证据与复核图](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/agents/graphs/evidence.py)：本地预处理、关联结果校验和身份复核。
- [tools.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/agents/tools.py)的`SonarAssociationAgent`执行模型调用；[prompts.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/agents/prompts.py)的`build_association_prompt()`组装契约。
- [graphs/tracking.py · 定位图](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/agents/graphs/tracking.py)：定位、连续估计和重投影节点。

这条路线中，大模型负责选择真实候选编号和双阵配对，不应直接猜经纬度。返回解释文字而非规定JSON、引用不存在编号、配对不同步或重复使用证据，都可能被后续校验拒绝。因此“模型已返回”不等于“能够绘图”。

### 4.2 完整本地反演流程

```text
图片和平台参数
  → 相对强度与亮带测量
  → 展开角路径和交叉竞争假设
  → 双阵轨迹级关联提案
  → 批量运动反演与独占归属
  → 原始图像联合细化
  → 可选模型复核
  → BTR回投检查
  → 主模型图、全部镜像图、轨迹和报告
```

核心调度是 [forward_inverse_agent.py · 本地反演编排](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/agents/forward_inverse_agent.py)中的`ForwardInverseAgent.run()`。默认不调用模型，不要求API Key或云端费用同意。只有另外申请复核时，才检查服务端模型配置与授权。

页面“识别流程”需要选择完整正向反演；只改变“模型前测量诊断”不会切换主路线。当前界面仍保留旧路线作为默认对照。

### 4.3 单文件数据绘图

- [data_input.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/inputs/data_input.py)：读取JSON、JSONL或受限简单JS包装数据，规范化点位并判断来源。
- [meta_tracks.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/inputs/meta_tracks.py)：根据航点或受限数学公式重建轨迹。
- [input_service.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/inputs/input_service.py)：`prepare_input()`区分图片和文件模式，统一上下文。

文件绘图仍有模型核对调用，但轨迹依据来自文件本身。meta模式的完整轨迹由本地参数重建，不能作为声纳盲反演准确率证明。上传的任意JavaScript不会被执行；数学公式使用受限解释器。

## 5 一次完整反演请求怎样执行

### 5.1 前端提交

[app.js](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/frontend/app.js)中的`submit(dryRun)`根据选项选择接口：

| 条件 | 接口 |
|---|---|
| 预检 | `POST /api/recognize`，带dry_run |
| 完整反演 | `POST /api/invert/stream` |
| 原模型流程 | `POST /api/recognize/stream` |

前端用FormData上传文件，不负责计算空间位置。图片模式的空间反演应使用同一样本的hull、tow和input/platform.json，不上传truth、meta或参考轨迹作为推断输入。

### 5.2 请求准备

[inversion.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/api/routers/inversion.py)中的`prepare_job()`完成以下操作：

1. 检查两张图及明确的工具配置。
2. 解析平台、时间方向和标定参数。
3. 校验格式、上传体积和像素数。
4. 获取进程内并发槽，保存临时文件。
5. 建立计算预算与取消对象。
6. 必要时配置一次可选模型复核。

完整反演首期支持一个平台的hull/tow双图。图片单张限制10MiB，总计20MiB，每张最多1600万像素；未知配色不能自动按工具强度模型处理。

### 5.3 工作线程与SSE

同文件中的`invert_stream()`把工作流交给工作线程执行，通过队列输出节点事件、心跳、结果和错误。前端`readModelStream()`消费SSE事件，`receiveNode()`和`drawNodes()`更新节点列表。

用户停止或浏览器断开时设置取消标记。Python工作线程不会因HTTP断开自动终止，因此代码等待其真正退出后才清理上传文件和释放并发槽，避免线程仍在读图时文件已被删除。

## 6 九个节点的具体实现

| 节点 | 函数与文件 | 输入与输出重点 |
|---|---|---|
| 输入与标定 | 工作流内部`validate()`；[rules.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/knowledge/rules.py)的`retrieve_rules()` | 校验context，保存标定与内置规则来源 |
| 完整亮带剖面测量 | [btr_field.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/measurements/btr_field.py)的`extract_fields()` | 图片变为带来源、时间、中心、宽度和质量标记的测量 |
| 展开角路径与交叉假设 | [ridge_hypotheses.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/association/ridge_hypotheses.py)的`ridge_hypotheses()` | 测量变为跨时间路径及竞争区域 |
| 双阵轨迹级关联候选 | 同文件的`propose_pairs()` | 提出具有同步支持的hull/tow路径配对 |
| 批量运动模型反演 | [batch_inverse.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/localization/batch_inverse.py)的`fit_proposals()` | 为关联提案拟合空间运动参数 |
| 全局归属与有界迭代 | 同文件的`select_exclusive()`、`refine_associations()`；[profile_inverse.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/localization/profile_inverse.py)的`refine_profiles()` | 处理证据冲突并进行原图联合细化 |
| 可选模型假设复核 | [hypothesis_review.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/agents/hypothesis_review.py) | 复核程序假设ID，不输出新坐标 |
| 正向BTR与可辨识性检查 | [inverse_quality.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/quality/inverse_quality.py)的`check_and_preview()` | 生成预测BTR、回投图、残差和未解释测量 |
| 空间候选绘图与汇总 | `build_document()`及[trajectory.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/rendering/trajectory.py)的`render_document()` | 生成轨迹JSON、主模型和全部镜像图 |

工作流状态由`InverseState`组织，主要数据流为context、evidence、ridges、proposals、fitted、selection、quality和document。中间NumPy私有数组不会直接写进公开JSON。

## 7 核心算法怎样实现

### 7.1 图像转换为亮带测量

[btr_field.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/measurements/btr_field.py)的`image_geometry()`按颜色范围找绘图区，并根据文件标记识别hull/tow。`extract_field()`默认提取65个同步时间切片。

[btr_measurements.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/measurements/btr_measurements.py)提供`decode_relative()`和`fit_profile()`：按声明的工具配色和相对dB范围解释像素，在局部剖面拟合一至两个高斯分量。输出的是相对强度，不是绝对声压；宽度、残差和误差代理不是经过真实样本标定的置信区间。

### 7.2 时间关联与目标数量

`ridge_hypotheses()`采用有预算的beam搜索保留路径解释，关联代价考虑中心变化、变化趋势、带宽及混合情况。`nearest_unfolded()`在折叠角的展开候选中寻找连续解释，避免把0或180度折返直接当作换目标。

`propose_pairs()`提出双阵轨迹级配对，`select_exclusive()`用整数优化选择相容候选。一个观测不能重复作为多个目标的独立数量证据；少量共享交叉证据会对所有相关身份对称排除。

目标数来自程序筛选后的身份假设集合，不是预设四个，也不是把两阵、分段或镜像相加。输出数量与真实目标数量相同，仍不等于身份正确。

### 7.3 正向几何和批量空间反演

[forward_observation.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/localization/forward_observation.py)实现：

- `GeoReference`：经纬度与局部米制坐标转换。
- `array_location()`：计算平台运动和阵列偏移后的观测位置。
- `initial_bearing()`：计算阵列指向目标的地理方位。
- `fold_relative()`：扣除航向后折叠到0至180度。
- `observe()`：汇总两阵的理论观测。

反演并不是从图片直接猜坐标。`evaluate_path()`根据参数构造空间运动，`predicted_bearings()`计算两阵预测方位，`fit_pair()`使用稳健最小二乘减小预测与测量差异，并比较匀速、二次、单次转向和谐波等运动模型。射线交点主要用于初值，不再依赖逐点交点硬连整条轨迹。

### 7.4 原图联合细化

`refine_profiles()`在已选身份内继续使用实际图像剖面，默认最多257行：

- 同时解释多个目标的高斯亮带，逐行亮度作为可变参数。
- 对显式工具配置拟合有符号周期背景。
- 对强度上下截断进行单侧处理。
- 不让不可分辨交叉混合峰驱动两份独立距离证据。
- 比较运动族复杂度和留出图像行的残差。
- 保留已经完成验证的更新，不修改原始观测为预测角度。

为减少耗时，代码缓存不变的其他目标模板和矩阵，每次只更新当前目标相关的一行、一列和右端项。它不是通过降低目标数量或复制已知轨迹加速。

### 7.5 质量检查的边界

`check_and_preview()`将空间候选重新投回两阵，计算图像解释、方位残差和测量覆盖，并生成诊断PNG。回投一致只能说明候选与当前观测解释相容；错误关联、某些镜像和错误ROI也可能得到较小残差，不属于独立真值验证。

## 8 Agent LangGraph与知识库的实际程度

### 8.1 LangGraph负责流程而非自动规划

当前使用真实StateGraph节点，节点执行会产生状态和耗时事件。但主要控制路径是固定顺序、有界迭代，并不是大模型自主决定工具和下一步的开放式规划系统。当前未实现持久化checkpointer和跨请求断点续跑。

### 8.2 节点不等于独立Agent

绝大多数节点是本地Python算法。旧路线的关联Agent调用模型选择候选编号；新路线的可选复核只能对已有假设作辅助评价。`validate_review()`拒绝未知ID、重复证据、非法区域、坐标及未知字段。

可选复核失败时保留本地数值结果，不自动付费重试；模型不能覆盖程序坐标或按文字否定数值身份。模型连接和协议由服务端配置与网关管理，不允许请求覆盖Key、模型或地址。

### 8.3 当前知识库是内置规则库

`knowledge.py`将代码中的版本化规则写入内存SQLite，按处理阶段检索。规则涵盖同步配对、交叉、折返、镜像、缺测、数量和回投等原则。

规则文本主要作为上下文和复核指导；真正的独占、同步、预算和数值限制仍由校验与算法代码执行。SQLite在这里不是持久案例数据库，更不是向量检索服务。

案例导入、相似检索、精确样本复用、参考轨迹直接返回和自动训练尚未实现。对应设计见 [案例知识库方案](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/docs/case-knowledge-base-plan.md)，不能将方案内容视为已上线功能。

## 9 前端展示与接口

| 接口 | 作用 |
|---|---|
| `GET /api/health` | 服务和能力信息，不表示准确率合格 |
| `POST /api/recognize` | 原路线或免费预检 |
| `POST /api/recognize/stream` | 原路线SSE |
| `POST /api/invert` | 完整反演同步返回 |
| `POST /api/invert/stream` | 完整反演进度与结果 |
| `GET /api/history` | 已生成结果列表 |
| `GET /api/history/{result_id}` | 载入保存结果，不新增模型调用 |
| `GET /api/results/{result_id}/{filename}` | 白名单结果文件 |
| `POST /api/compare` | 彩色轮廓比较 |
| `POST /api/evaluate` | 本地结构化评测 |
| `GET /api/logs/{request_id}` | 请求日志 |

前端`showModelResult()`显示图片、告警、用量和下载。主图只显示程序排序的代表运动假设，并明确说明侧别未唯一验证；全部竞争镜像单独提供图像，轨迹JSON仍保留所有分支。展示代表假设不等于物理镜像已消除。

## 10 结果存储与关键字段

结果目录位于 `C:\Users\LENOVO\Desktop\btr_gens\qwen_image_client\outputs`。每次请求创建独立结果ID，格式包含日期、时间和随机标记。

| 产物名称 | 内容 |
|---|---|
| tracks.json | 所有保留轨迹分支，含身份、分支、镜像与点位 |
| estimated_track.png | 主运动模型代表图，不代表唯一正确解 |
| mirror_candidates.png | 全部保留镜像候选图 |
| execution_report.json | 节点状态、摘要、耗时和数量报告 |
| inverse_fields.json | 测量来源、时间、中心、宽度和质量 |
| inverse_ridges.json | 时间路径与交叉竞争区域 |
| inverse_proposals.json | 双阵轨迹级提案 |
| inverse_fit.json | 图像细化前的批量拟合记录 |
| inverse_selection.json | 最终选择及独占证据 |
| inverse_profile_fit.json | 原图细化、运动族比较和阶段预算状态 |
| inverse_quality.json | 回投、一致性和未解释测量 |
| calibration_context.json | 平台和标定上下文 |
| knowledge_sources.json | 本次使用的内置规则来源 |
| answer.txt | 新路线中是本地报告，不冒充大模型正文 |

新路线轨迹通常使用`coordinate_system=lon_lat`，点中的x是经度、y是纬度、t是秒。`mirror_variant`区分竞争分支；`point_source`说明点位是观测时刻的模型估计。输出空间坐标不代表唯一可观测性已被证明，需同时查看`unique_solution`、告警和优化状态。

[result_repository.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/repositories/result_repository.py)负责白名单文件与历史读取；[results.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/api/routers/results.py)将其转换为HTTP响应。

## 11 时间预算 异常与安全边界

[inversion_control.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/jobs/inversion_control.py)提供总预算、剩余时间和取消标记。当前总预算120秒，图像细化最多60秒，并预留15秒给后续处理。

- 细化阶段耗尽时，保留已完整比较并接受的更新，其余沿用已有有效候选，返回`optimization_status=budget_limited`。
- 前端明确显示部分优化，不能宣称完整细化成功。
- 用户取消和真正的总预算错误仍中止，不作为正常阶段截止吞掉。
- 剩余时间不足50秒时，不再发起可选云端复核。
- 前序没有有效候选或预算已经耗尽时，不伪造图片；阶段预留也不是所有硬件条件下的实时保证。

进程内共享并发槽目前为2，见 [runtime.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/core/runtime.py)。它不是持久任务队列或分布式调度器。

服务端配置不在网页填写，日志进行凭据脱敏。服务限制本地主机和同源请求，下载有文件白名单与路径检查。不过当前没有完整账号、权限、持久任务和生产审计体系，不能直接等同于可公开部署的多用户服务。

## 12 评测与验证方式

必须区分三个层次：程序运行成功、回投与观测一致、与独立参考轨迹一致。三者不能互相代替。

- [comparison.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/evaluation/comparison.py)：彩色轮廓归一化比较，受图例、裁剪、尺度和镜像影响，不是定位准确率。
- [evaluation.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/evaluation/evaluation.py)：网页结构化评价。遇到未消歧的多分支时不直接按真值挑唯一答案。
- [evaluate_inverse.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/scripts/evaluate_inverse.py)：离线记录主分支误差及所有镜像误差，最佳镜像值明确为使用标签选侧后的评价下界。
- [compare_sample.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/scripts/compare_sample.py)：先使用input推断，结束后才读取truth，输出时间对齐误差与同米制坐标框的轮廓对照。比较不会回写生产轨迹。

最近一次验证为Python182项通过、前端JS语法及流式回归通过。指定sample_0005的真实浏览器运行约25.34秒，产生4个身份假设和22条细化进度，新增模型调用0次。该样本经过反复调试，不能作为独立泛化测试。

验证记录见 [指定样本迭代报告](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/docs/sample005-iteration-results.md) 和 [超时修复记录](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/docs/inverse-timeout-fix.md)。

## 13 当前不足与后续重点

1. 主要适配已声明工具配色与生成分布，自动配色识别、理论线分离和真实声纳适用性未完成。
2. 短基线、弱视差、X交叉和弱目标仍可能导致错配、漏检、断段和距离不稳定。
3. 折叠方位仍存在等价或近等价解释，主模型排序不是独立侧别证据。
4. 参考track图可能绘制不可见全时段，程序不能为了图像一致而将补造点伪装成检测。
5. 网页和离线脚本对多分支采用不同评价口径，需要进一步统一。
6. 当前知识库没有案例导入、检索和复用；接入前需明确标签隔离和精确复用边界。
7. 依赖采用版本范围而非完整锁定环境；长期复现还需固化依赖和测试数据版本。

建议优先统一入口说明和评价口径，建立独立轨迹族的回归集，再验证案例库与模型复核是否带来可测收益，而不是单纯增加Agent节点。

## 14 推荐代码阅读顺序

1. [main.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/main.py)：理解服务如何启动。
2. [app.js](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/frontend/app.js)：从`submit()`追踪选项、请求和SSE展示。
3. [inversion.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/api/routers/inversion.py)：理解HTTP、上传、线程与取消边界。
4. [forward_inverse_agent.py · 本地反演编排](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/agents/forward_inverse_agent.py)：理解九节点和状态传递。
5. [btr_field.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/measurements/btr_field.py)：理解图片如何变成测量。
6. [ridge_hypotheses.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/association/ridge_hypotheses.py)：理解时间路径和双阵提案。
7. [batch_inverse.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/localization/batch_inverse.py)：理解空间拟合与独占选择。
8. [profile_inverse.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/localization/profile_inverse.py)：理解原图细化、背景、缓存和阶段预算。
9. [inverse_quality.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/quality/inverse_quality.py)：理解回投检查与局限。
10. [trajectory.py](C:/Users/LENOVO/Desktop/btr_gens/qwen_image_client/backend/app/services/rendering/trajectory.py)：理解最终图像与分支展示。

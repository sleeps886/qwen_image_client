# 身份归并与单条连续轨迹

## 不是绘图层补线

旧版将同一身份的观测片段分别放入 `tracks`，再额外绘制虚线。
当前在 `batch_inverse.build_document()` 输出轨迹文档之前执行身份连续性归并，
渲染器只画归并后的点序列，不再生成或绘制补接虚线。

归并条件：目标身份、平台、双阵关联假设 ID、镜像分支和整段运动参数一致，
两端位置与同一个模型匹配，时间不重叠，缺口在当前 90 秒工程限值内。
不是根据颜色、目标名称、空间距离近或预设目标数归并。

这只确认片段属于同一个程序运动/关联假设，不声称独立验证了物理目标身份。
不同关联假设、镜像、参数不匹配或证据不足的片段不会强行合并。

## 数据结构

- `tracks`：归并后的连续轨迹；主视图每个可归并身份一条实线，镜像仍各自保留。
- `tracks[].observation_segments`：原有观测支持段及其原始点，保留原始时间间隔。
- `tracks[].inferred_intervals`：没有观测支持、沿同一个运动模型估计的区间。
- `tracks[].points[].observation_supported`：区分观测支持时刻与缺测模型估计点。
- `identity_continuity`：记录输入片段数、输出轨迹数、归并数量、关联假设及未决片段。

重复处理不会将估计点提升为观测点。评测从 `observation_segments` 取数据，
缺测估计不增加位置评测和观测覆盖率；离线评测也采用同样的过滤。
PNG 视觉连续性改善不等于独立位置误差改善，原有镜像与空间解偏差仍可能存在。

## 代码

- `services/localization/identity_continuity.py`：同身份片段归并。
- `services/localization/gap_estimates.py`：在整段模型上计算缺测区间点。
- `services/localization/batch_inverse.py`：数值轨迹输出时调用归并。
- `services/evaluation/observation_support.py`：隔离观测支持和推断点。
- `services/rendering/trajectory.py`：绘制单条轨迹，不再另画虚线。
- `agents/forward_inverse_agent.py` 和历史接口：返回归并报告供运行面板展示。

## 现有结果复核

只读取 `20261119_155407_6dbd5a79` 原轨迹和平台参数，未读取真值参与归并，
未重新推理或调用模型，未覆盖原历史输出。

- 主分支：6 个片段 → N1、N2、N3、N4 四条轨迹。
- N2 的两段和 N4 的两段分别归并，每个身份保持单条连续点序列。
- 主图没有补接虚线，缺测状态保存在数据中，不伪装成观测。
- 预览和核对报告位于 `work/identity-continuity-155407/`。

新生成结果自动使用该结构；旧历史 PNG 不会自动重画。

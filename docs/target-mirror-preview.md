# 每目标独立镜像预览

## 使用

前端修改已接入原 `/incremental` 工作台；无需后端重启或再次调用模型。Ctrl+F5刷新，在右侧空间图上方展开 **逐目标镜像预览（免费，不改模型结果）**。

每个目标有独立选项：

- **保留原分支**：显示模型已保存的原位置分支；旧协议保持其原显示。
- **整条镜像（人工预览）**：只切换该目标各可见已定位点到双阵基线对应的已有镜像候选，保留目标颜色和接续关系。
- **同时查看两个候选**：实线为原分支，淡色虚线为镜像；是同一目标的两个位置假设，不是两个目标，不重复计算已处理组数。

本次用户100组会话中，蓝色为 `B000003`，可单独选择整条镜像；绿色和黄色不变。该选择必须标记为人工显示，不得冒充模型自动消歧。

## 边界

1. 反射轴按各时刻平台位置、运动、航向、hull/tow偏移和局部坐标原点计算，不是屏幕上下翻转。
2. 反射结果必须匹配该组后端已经提供的有效交点候选，否则不切换、不编造坐标；没有导航、基线不足或无法为整条已定位轨迹找到镜像时禁用选项。
3. 模型未选分支、几何无交点、未通过校验的组，不会通过镜像功能获得位置或接线。
4. 显示状态保存在浏览器的本会话、本运行、本已处理游标下；刷新可恢复。模型推进新批次时旧人工选择暂停，提示重新核对，不能把对旧分支的反射盲目套到新分支。
5. 原观测、目标归属、模型原文、数据库快照和模型选择的分支不变。不调用模型、不发业务POST、不做人工目标确认。
6. 业务上下文复制中同时保留原模型候选、人工显示候选、镜像选项和操作记录，避免将人工显示当成模型输出。
7. 整体镜像歧义仍然存在；手动切换不能代替左右侧、有向方位或独立观测证据。

## 实际验证（2026-11-26）

- 使用用户真实保存会话 `20261126_193818_d538fbe8` 的100组、99个位置，蓝色34点全部切换到对应已有镜像；绿色、黄色位置和颜色不变。
- 浏览器验证：单目标切换、两个候选叠加、恢复原分支、刷新恢复、新快照游标变更后暂停旧选择、100组明细无增减，均通过。
- 另直接连接实际运行的 `http://127.0.0.1:8870` GET读取同一会话，验证当前前端正常工作；不是仅在隔离测试页面验证。
- 所有API POST在验证浏览器中被拦截，实际POST数量0；新增模型调用0。原state、run哈希与调用计数验证不变。
- 专项Node测试覆盖斜向移动基线、模型输入不变、缺少几何不补点、缺导航不捏造镜像、分目标隔离、双候选不增加处理数量、接续不跨缺失组。
- 相关后端回归24项通过（未修改推断或持久化模型协议）。

验证命令：

```powershell
node frontend/tests/test_target_mirror_preview.cjs
node frontend/tests/test_model_owned_branches.cjs
node frontend/tests/test_spatial_group_display.cjs
node frontend/tests/test_sequence_status_recovery.cjs
python -m pytest backend/tests/test_workbench_spatial_cards.py backend/tests/test_sequential_spatial_preview.py backend/tests/test_spatial_hypotheses.py -q
```

浏览器证据：

- `output/supplied-zip-test/target-mirror-blue-flipped.png`
- `output/supplied-zip-test/target-mirror-both.png`
- `output/supplied-zip-test/target-mirror-live-8870.png`

前端实现位于 `frontend/point-sampling.js` 的 `reflectedCandidate` / `targetMirrorPreview` 以及 `frontend/mixed-workspace.js` 的 `drawTargetMirrorControls`。

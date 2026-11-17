# 前端

- `index.html`：页面结构。
- `app.js`：上传、流式结果、节点过程、候选图及历史记录交互。
- `style.css`：页面样式。
- `tests/test_frontend_stream.cjs`：前端协议与交互回归，运行`node frontend/tests/test_frontend_stream.cjs`（项目根目录）。

保留原有HTML/JS/CSS实现，没有新增Node构建依赖。
由后端提供根页面与`/assets/app.js`、`/assets/style.css`，仍同源访问`/api/*`。
请从项目根目录运行`python -m backend.app.main`或双击`启动工具.cmd`，不要直接双击HTML。
前端文件变更刷新页面即可；模型配置与密钥只留后端。

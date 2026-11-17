# 前后端目录与执行边界

```text
qwen_image_client/
├─ backend/
│  ├─ app/
│  │  ├─ agents/sonar_agent.py  # Agent提示词、输出契约、模型调用入口
│  │  ├─ api/                 # FastAPI路由、依赖
│  │  ├─ core/                # 配置、路径、异常、中间件
│  │  ├─ infrastructure/llm/  # 模型HTTP协议、认证、SSE
│  │  ├─ models/
│  │  ├─ repositories/        # 结果读写、安全路径
│  │  ├─ schemas/
│  │  ├─ services/            # 图像、身份、定位、评测等本地算法
│  │  ├─ workflows/           # LangGraph编排、执行记录
│  │  ├─ legacy/              # 旧桌面UI
│  │  └─ main.py              # 应用装配和服务入口
│  ├─ scripts/                # 图片CLI、本地重处理
│  ├─ tests/                  # 后端Python测试
│  ├─ requirements.txt
│  └─ requirements-dev.txt
├─ frontend/
│  ├─ index.html
│  ├─ app.js
│  ├─ style.css
│  └─ tests/                  # 前端JS交互与协议回归
├─ docs/
├─ examples/
├─ logs/
├─ outputs/                   # 既有结果不搬动
├─ work/                      # 临时上传、测试预览，不搬动
├─ .env                       # 后端读取，绝不作为静态文件暴露
├─ app.py                     # python app.py兼容入口
├─ 启动工具.cmd
├─ requirements.txt           # 转发到backend/的兼容依赖入口
└─ requirements-dev.txt
```

## 启动与验证

所有命令在项目根目录执行：

```powershell
python -m pip install -r requirements.txt
python -m backend.app.main
python -B -m unittest discover -s backend/tests -t . -q
node --check frontend/app.js
node frontend/tests/test_frontend_stream.cjs
python -m backend.scripts.send_images --help
python -m backend.scripts.reprocess_identity 结果编号
```

双击启动工具.cmd和python app.py仍可用。原内部模块命令python -m app.main已迁移为python -m backend.app.main；不保留重复Python包别名，避免同一模块加载两份。

## 前后端通信

- 前端仍为原生HTML/JS/CSS，不引入框架、构建器或额外开发服务器。
- FastAPI从frontend/读取根页面，并仅允许app.js/style.css这两个静态资源名。
- /api/*、端口8870、同源安全检查、API费用确认、历史下载URL均不变。
- .env、outputs、work、logs与examples仍按项目根目录定位，不依赖shell当前目录。
- Agent只属于后端。services保留确定性计算，workflows负责编排；此次不修改识别算法或准确性结论。

## 实现说明

- [Agent阶段边界](agent-evidence.md)
- [连续估计与重投影](continuous-tracking.md)
- [数量与执行记录](target-count-progress.md)
- [身份复核及离线重处理](identity-association.md)

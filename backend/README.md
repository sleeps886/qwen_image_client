# 后端

```text
backend/
  backend/app/
    agents/          # Agent入口、nodes/、graphs/、工具、状态和提示词
    api/             # FastAPI路由和依赖
    core/            # 配置、路径、错误、中间件
    infrastructure/  # 模型网关与协议
    models/
    repositories/
    schemas/
    services/        # 数值/图像处理与本地复核
    legacy/
    main.py
  scripts/           # 显式CLI与无费用本地重处理
  tests/             # 后端Python回归
  requirements.txt
  requirements-dev.txt
```

命令均从项目根目录执行：

```powershell
python -m pip install -r backend/requirements.txt
python -m backend.app.main
python -B -m unittest discover -s backend/tests -t . -q
node frontend/tests/test_frontend_stream.cjs
python -m backend.scripts.send_images --help
python -m backend.scripts.reprocess_identity 结果编号
```

项目根目录的app.py、requirements.txt和启动工具.cmd保留兼容入口。
.env、outputs、work、logs、examples仍在项目根目录，不搬动密钥与历史数据。
前端文件位于../frontend，由FastAPI继续同源提供，API地址不变。

# 测试目录

测试代码不参与正常启动，运行应用使用 `python -m backend.app.main` 或“启动工具.cmd”；`python app.py`兼容保留。

从项目根目录执行：

```powershell
python -B -m unittest discover -s backend/tests -t . -v
node frontend/tests/test_frontend_stream.cjs
```

单独运行某个模块：

```powershell
python -B -m unittest backend.tests.test_meta_tracks -v
```

Python测试使用模拟模型接口；前端测试不发送真实请求，不产生API费用。
历史测试日志已归档到 `logs/tests/`，日志由Git忽略，不再散落项目根目录。

# 统一业务异常处理

## 分层

- `backend/app/core/exceptions.py`：异常基类、分类、状态码及反演业务默认提示，不依赖 FastAPI。
- `backend/app/api/exception_handlers.py`：统一转换为 HTTP 响应；集中处理请求字段验证失败。
- `backend/app/main.py`：注册处理器。
- `backend/app/services/jobs/inverse_jobs.py`：截图中的反演输入准备、文件校验、复核授权及任务资源管理。
- `backend/app/api/routers/inversion.py`：接收请求、调用 service 和工作流，保留 SSE 生命周期管理。

## 异常分类

所有业务异常继承 `ApplicationError(Exception)`，接口层不再直接构造 `HTTPException`。

| 分类 | 状态码 | 用途 |
| --- | --- | --- |
| `InputError` | 400 | 参数、格式、授权确认等输入问题 |
| `NotFoundError` | 404 | 结果或资源不存在 |
| `PayloadTooLargeError` | 413 | 上传超限 |
| `ProcessingError` | 422 | 输入已接收，但无法完成处理 |
| `BusyError` | 429 | 并发任务已满 |
| `LocalStorageError` | 500 | 本地文件操作失败 |
| `ModelServiceError` | 502 | 模型服务失败 |
| `ModelTimeoutError` | 504 | 模型服务超时 |

反演细分异常包括 `ReviewConsentRequired`、`InvalidImagePair`、
`UnsupportedRendererProfile`、`UnsupportedPlatformCount`、`InvalidImageFormat`、
`ReviewCredentialsRequired`、`InversePreparationError`、`InverseProcessingError`。
服务直接抛出语义异常，不自行选择 HTTP 状态码或重复编写默认提示：

```python
from backend.app.core.exceptions import InvalidImagePair

if len(files) != 2:
    raise InvalidImagePair()
```

旧 `ClientError`、上游连接/超时和仓储异常也归入此继承体系；仓储异常保留
`ValueError` 兼容继承，避免破坏现有调用方。

## 响应及边界

- 保留既有 `{"detail": "提示"}` 响应，不要求前端改协议。
- 反演运行失败仍返回 422，`detail` 保留 `message`、`result_id`、`automatic_retry`。
- SSE 已发出响应头后不能改变 HTTP 状态码；运行期失败仍通过 `error` 事件报告，不能伪装成功。
- 文件句柄总是关闭；只有成功取得任务槽的调用才能释放槽；成功任务在 worker 真正结束后释放。
- 未预期异常不向客户端暴露内部文件路径；已有模型错误脱敏逻辑保留。
- 上传总请求体、同源限制、请求日志等 ASGI 中间件边界仍直接返回安全响应；不在外层中间件抛出期望由内层处理器捕获的异常。
- 数值算法内部 `ValueError` 等编程/计算校验没有机械替换；由业务或接口边界转换为业务异常。

## 验证

`backend/tests/test_exceptions.py` 覆盖分类状态码、原有响应形状、反演细分异常，
以及授权失败、繁忙、输入失败和成功关闭时的资源清理。不调用真实云端模型。

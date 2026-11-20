"""Application errors, independent of FastAPI and any model SDK."""


class ApplicationError(Exception):
    """Safe public error; HTTP adapters consume status_code and detail."""
    status_code=500
    default_message='服务内部异常，请查看运行日志；不要盲目重试。'

    def __init__(self,detail=None):
        self.detail=self.default_message if detail is None else detail
        super().__init__(self.detail)


class InputError(ApplicationError):
    status_code=400
    default_message='输入参数无效'


class NotFoundError(ApplicationError):
    status_code=404
    default_message='资源不存在'


class PayloadTooLargeError(ApplicationError):
    status_code=413
    default_message='单图不得超过10MiB，合计不得超过20MiB'


class ProcessingError(ApplicationError):
    status_code=422
    default_message='无法完成当前数据的处理'


class BusyError(ApplicationError):
    status_code=429
    default_message='本地计算已有两个任务，请勿重复提交'


class LocalStorageError(ApplicationError):
    default_message='本地文件操作失败，请检查权限与磁盘；不要盲目重复发送。'


class ModelServiceError(ApplicationError):
    status_code=502
    default_message='模型服务连接失败，未自动重试'


class ModelTimeoutError(ModelServiceError):
    status_code=504
    default_message='模型服务响应超时，未自动重试'


# Forward-inversion input errors. Keep user messages here, not in services.
class ReviewConsentRequired(InputError):
    default_message='申请模型复核或最终轨迹生成必须明确同意云端传输与费用；本地反演不需要勾选'


class InvalidImagePair(InputError):
    default_message='正向反演需要hull/tow各一张图'


class UnsupportedRendererProfile(InputError):
    default_message='请选择本工具原始BTR渲染配置；不对未知配色自动反演'


class UnsupportedPlatformCount(InputError):
    default_message='首期完整正向反演只支持一个平台'


class InvalidImageFormat(InputError):
    default_message='仅支持PNG/JPEG/WebP且每图最多1600万像素'


class ReviewCredentialsRequired(InputError):
    default_message='云端模型需要服务端API Key；可切换本地正向反演'


class InversePreparationError(InputError):
    default_message='无法准备本地反演输入'


class InverseProcessingError(ProcessingError):
    def __init__(self,message,*,result_id=None):
        super().__init__({'message':message[:250],'result_id':result_id,'automatic_retry':False})


class ClientError(InputError):
    pass


class UpstreamTimeout(ClientError):
    status_code=504


class UpstreamConnectionError(ClientError):
    status_code=502


class ResultNotFound(NotFoundError,ValueError):
    pass


class InvalidStoredResult(InputError,ValueError):
    pass

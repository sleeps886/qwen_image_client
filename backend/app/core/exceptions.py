"""Application errors, independent of FastAPI and any model SDK."""


class ClientError(Exception):
    pass


class UpstreamTimeout(ClientError):
    pass


class UpstreamConnectionError(ClientError):
    pass


class ResultNotFound(ValueError):
    pass


class InvalidStoredResult(ValueError):
    pass

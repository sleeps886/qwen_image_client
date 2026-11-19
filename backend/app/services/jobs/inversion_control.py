"""Shared cancellation and resource bounds for local inverse processing."""
from dataclasses import dataclass,field
from threading import Event
from time import monotonic


class InversionStopped(ValueError):
    pass


@dataclass
class InversionControl:
    seconds:float=120.
    cancelled:Event=field(default_factory=Event)
    started:float=field(default_factory=monotonic)

    @property
    def remaining(self):return max(0.,self.seconds-(monotonic()-self.started))

    def check(self):
        if self.cancelled.is_set():raise InversionStopped('本地反演已取消，未自动重试')
        if monotonic()-self.started>self.seconds:raise InversionStopped('本地反演达到计算时间预算，未伪造完整结果')

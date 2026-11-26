"""A browser observes one running job; disconnecting never starts/cancels it.

Tasks survive HTTP disconnects, not server shutdown. Domain generators retain
responsibility for durable outcomes and releasing their own execution locks.
"""
import asyncio

LIVE_TASKS=set()


def detached_sse(source,release_unstarted,*,heartbeat_seconds=10):
    launched=False

    async def release_if_unstarted():
        if not launched:release_unstarted()

    async def connected():
        nonlocal launched
        launched=True
        queue=asyncio.Queue()
        attached=True
        end=object()

        async def produce():
            try:
                async for item in source:
                    # Domain streaming caps cumulative body size. Once the
                    # browser detaches, stop buffering its progress events.
                    if attached:queue.put_nowait(item)
            finally:
                if attached:queue.put_nowait(end)

        task=asyncio.create_task(produce())
        LIVE_TASKS.add(task)
        def finished(done):
            LIVE_TASKS.discard(done)
            if not done.cancelled():done.exception()  # retrieve; domain saved outcome
        task.add_done_callback(finished)
        try:
            while True:
                try:item=await asyncio.wait_for(queue.get(),heartbeat_seconds)
                except TimeoutError:
                    yield ': keep-alive\n\n'
                    continue
                if item is end:
                    # Propagate unexpected producer errors to the observer;
                    # cancelling the observer must never cancel this task.
                    if not task.cancelled() and task.exception():raise task.exception()
                    break
                yield item
        finally:
            attached=False
            while not queue.empty():queue.get_nowait()

    return connected(),release_if_unstarted

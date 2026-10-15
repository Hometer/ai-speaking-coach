"""共享截止时间；隔离无截止 SDK 的等待，不让迟到供应商结果接触数据库。"""
from contextlib import contextmanager
import contextvars
import queue
import threading
import time
from app.core.errors import AppError

_deadline = contextvars.ContextVar('request_deadline', default=None)
_slots = threading.BoundedSemaphore(4)


def begin(seconds=15):
    previous = _deadline.get()
    return _deadline.set(min(previous, time.monotonic()+seconds) if previous else time.monotonic()+seconds)


def reset(token):
    _deadline.reset(token)


def remaining(maximum=15):
    end = _deadline.get()
    value = min(maximum, end-time.monotonic()) if end else maximum
    if value <= 0:
        raise AppError('PROVIDER_TIMEOUT', '本次处理已超时，可查看已保存结果或稍后重试', 504)
    return value


def bounded(call, maximum=15):
    timeout=remaining(maximum)
    if not _slots.acquire(blocking=False):
        raise AppError('PROVIDER_BUSY', '服务繁忙，请稍后重试', 503)
    result=queue.Queue(maxsize=1)
    def worker():
        try:
            result.put((True,call()))
        except BaseException as exc:
            result.put((False,exc))
        finally:
            _slots.release()
    context=contextvars.copy_context()
    threading.Thread(target=lambda:context.run(worker),daemon=True).start()
    try:
        ok,value=result.get(timeout=timeout)
    except queue.Empty:
        raise AppError('PROVIDER_TIMEOUT','本次处理已超时，迟到结果不会恢复练习',504)
    if not ok:
        raise value
    return value


@contextmanager
def until(end):
    previous=_deadline.get()
    token=_deadline.set(min(previous,end) if previous else end)
    try:
        yield
    finally:
        _deadline.reset(token)

"""SQLite 的无时区时间约定为 UTC；接口必须显式携带偏移。"""
from datetime import datetime, timezone


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()

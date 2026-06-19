"""
统一 UTC 时间语义

所有关键时间必须使用 UTC 并包含时区信息。未来时间戳直接拒绝。
"""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """返回当前 UTC 时间（含时区）。"""
    return datetime.now(timezone.utc)


def assert_utc(ts: datetime, label: str = "timestamp") -> None:
    """断言时间戳包含 UTC 时区信息，否则抛出 ValueError。"""
    if ts.tzinfo is None:
        raise ValueError(
            f"{label} 缺少时区信息：必须使用 UTC（{ts!r}）"
        )
    if ts.tzinfo != timezone.utc:
        raise ValueError(
            f"{label} 时区不是 UTC：{ts.tzinfo}（{ts!r}）"
        )


def assert_not_future(ts: datetime, label: str = "timestamp") -> None:
    """断言时间戳不在未来，否则抛出 FutureTimestampError。"""
    assert_utc(ts, label)
    now = utc_now()
    if ts > now:
        raise FutureTimestampError(
            f"{label} 在未来（{ts}），当前 UTC 时间 {now}"
        )


def as_utc(ts: datetime) -> datetime:
    """将无时区时间戳视为 UTC，或转换已有 TZ 的时间戳为 UTC。"""
    if ts.tzinfo is None:
        if ts.year > 2020:
            return ts.replace(tzinfo=timezone.utc)
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


class FutureTimestampError(ValueError):
    """时间戳在未来时抛出。"""
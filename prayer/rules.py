"""Iqamah rule implementations.

Each rule is a callable that takes an adhan ``datetime`` and returns the
iqamah ``datetime``.  New rule types can be added here without touching
:class:`~prayer.iqamah.IqamahEngine`.

Usage::

    rule = OffsetRule(20)
    iqamah = rule.apply(adhan_time)
"""

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from config.models import IqamahRuleConfig


@runtime_checkable
class IqamahRule(Protocol):
    def apply(self, adhan_time: datetime) -> datetime:
        ...


class OffsetRule:
    """Iqamah = adhan + *value* minutes."""

    def __init__(self, value: int) -> None:
        self._delta = timedelta(minutes=value)

    def apply(self, adhan_time: datetime) -> datetime:
        return adhan_time + self._delta


class RoundUpRule:
    """Round the adhan time up to the next *every_n_minutes* boundary.

    Example: adhan = 12:47, every_n = 15  →  iqamah = 13:00
             adhan = 12:45, every_n = 15  →  iqamah = 12:45 (already on boundary)
    """

    def __init__(self, every_n_minutes: int) -> None:
        if every_n_minutes <= 0:
            raise ValueError("every_n_minutes must be positive")
        self._n = every_n_minutes

    def apply(self, adhan_time: datetime) -> datetime:
        remainder = adhan_time.minute % self._n
        if remainder == 0:
            return adhan_time.replace(second=0, microsecond=0)
        add = self._n - remainder
        return (adhan_time + timedelta(minutes=add)).replace(second=0, microsecond=0)


class FixedTimeRule:
    """Override iqamah to a fixed clock time (same day as adhan)."""

    def __init__(self, hour: int, minute: int) -> None:
        self._hour = hour
        self._minute = minute

    def apply(self, adhan_time: datetime) -> datetime:
        return adhan_time.replace(hour=self._hour, minute=self._minute, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_rule(cfg: IqamahRuleConfig) -> IqamahRule:
    """Instantiate the correct :class:`IqamahRule` from a config object."""
    if cfg.type == "offset_minutes":
        return OffsetRule(cfg.value)  # type: ignore[arg-type]
    if cfg.type == "round_up_to":
        return RoundUpRule(cfg.every_n_minutes)  # type: ignore[arg-type]
    if cfg.type == "fixed_time":
        return FixedTimeRule(cfg.hour, cfg.minute)  # type: ignore[arg-type]
    raise ValueError(f"Unknown iqamah rule type: {cfg.type!r}")

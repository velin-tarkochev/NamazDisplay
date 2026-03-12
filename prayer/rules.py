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

    If the rounded time is less than *min_gap_minutes* after the adhan, advance
    to the next boundary until the gap requirement is satisfied.

    Example: adhan = 12:47, every_n = 15               →  iqamah = 13:00
             adhan = 12:45, every_n = 15               →  iqamah = 13:00 (gap=0 < min_gap=10)
             adhan =  7:29, every_n = 30, min_gap = 10 →  iqamah =  8:00 (7:30 gap=1 < 10)
    """

    def __init__(self, every_n_minutes: int, min_gap_minutes: int = 10) -> None:
        if every_n_minutes <= 0:
            raise ValueError("every_n_minutes must be positive")
        self._n = every_n_minutes
        self._min_gap_secs = min_gap_minutes * 60

    def apply(self, adhan_time: datetime) -> datetime:
        remainder = adhan_time.minute % self._n
        if remainder == 0:
            candidate = adhan_time.replace(second=0, microsecond=0)
        else:
            candidate = (adhan_time + timedelta(minutes=self._n - remainder)).replace(
                second=0, microsecond=0
            )
        # Advance to next boundary until minimum gap is satisfied
        while (candidate - adhan_time).total_seconds() < self._min_gap_secs:
            candidate += timedelta(minutes=self._n)
        return candidate


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
        min_gap = cfg.min_gap_minutes if cfg.min_gap_minutes is not None else 10
        return RoundUpRule(cfg.every_n_minutes, min_gap)  # type: ignore[arg-type]
    if cfg.type == "fixed_time":
        return FixedTimeRule(cfg.hour, cfg.minute)  # type: ignore[arg-type]
    raise ValueError(f"Unknown iqamah rule type: {cfg.type!r}")

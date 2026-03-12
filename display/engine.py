"""Abstract display interface.

:class:`DisplayEngine` defines the contract that all display backends must
satisfy.  The only concrete implementation right now is
:class:`~display.pygame_display.PyGameDisplay`.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from app_state import AppState
from prayer.calculator import PrayerTimes


@dataclass
class DisplayState:
    """Snapshot of :class:`~app_state.AppState` safe to pass to a renderer."""

    current_time: datetime
    prayer_times: Optional[PrayerTimes]
    iqamah_times: dict[str, Optional[datetime]]
    hijri: tuple[int, int, int]
    next_prayer_name: str
    countdown: timedelta
    interval_progress: float = 0.0  # 0.0–1.0: elapsed fraction of current inter-prayer interval
    jumuah_time: Optional[datetime] = None  # non-None on Fridays when Jumu'ah is enabled
    next_prayer_adhan: Optional[datetime] = None  # for live countdown computation in the display
    current_iqamah: Optional[datetime] = None       # set while between adhan and iqamah
    current_iqamah_name: str = ""
    current_iqamah_progress: float = 0.0            # 0.0 at adhan, 1.0 at iqamah

    @classmethod
    def from_app_state(cls, state: AppState) -> "DisplayState":
        snap = state.snapshot()
        return cls(
            current_time=snap.current_time,
            prayer_times=snap.prayer_times,
            iqamah_times=snap.iqamah_times,
            hijri=snap.hijri,
            next_prayer_name=snap.next_prayer_name,
            countdown=snap.countdown,
            interval_progress=snap.interval_progress,
            jumuah_time=snap.jumuah_time,
            next_prayer_adhan=snap.next_prayer_adhan,
            current_iqamah=snap.current_iqamah,
            current_iqamah_name=snap.current_iqamah_name,
            current_iqamah_progress=snap.current_iqamah_progress,
        )


class DisplayEngine(ABC):
    """Render a :class:`DisplayState` to some output device."""

    @abstractmethod
    def render(self, state: DisplayState) -> None:
        """Draw one frame."""
        ...

    @abstractmethod
    def run(self, app_state: AppState) -> None:
        """Main loop — blocks the calling thread until the display is closed."""
        ...

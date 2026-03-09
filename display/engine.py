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

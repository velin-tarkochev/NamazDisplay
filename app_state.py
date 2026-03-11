"""Shared application state, written by the scheduler and read by the display
and web layer.  All access is protected by a :class:`threading.Lock`.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from prayer.calculator import PrayerTimes


@dataclass
class AppState:
    # Set by scheduler after each daily recalculation
    prayer_times: Optional[PrayerTimes] = None
    iqamah_times: dict[str, Optional[datetime]] = field(default_factory=dict)
    hijri: tuple[int, int, int] = (0, 0, 0)  # (year, month, day); (0,0,0) if unavailable

    # Updated every second by the scheduler
    current_time: datetime = field(default_factory=datetime.now)
    next_prayer_name: str = ""
    next_prayer_adhan: Optional[datetime] = None
    next_prayer_iqamah: Optional[datetime] = None
    countdown: timedelta = field(default_factory=timedelta)
    interval_progress: float = 0.0  # 0.0–1.0: elapsed fraction of current inter-prayer interval
    jumuah_time: Optional[datetime] = None  # set on Fridays when Jumu'ah is enabled

    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def snapshot(self) -> "AppState":
        """Return a shallow copy for thread-safe reading by the display."""
        with self._lock:
            return AppState(
                prayer_times=self.prayer_times,
                iqamah_times=dict(self.iqamah_times),
                hijri=self.hijri,
                current_time=self.current_time,
                next_prayer_name=self.next_prayer_name,
                next_prayer_adhan=self.next_prayer_adhan,
                next_prayer_iqamah=self.next_prayer_iqamah,
                countdown=self.countdown,
                interval_progress=self.interval_progress,
                jumuah_time=self.jumuah_time,
            )

    def write(self, **kwargs) -> None:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

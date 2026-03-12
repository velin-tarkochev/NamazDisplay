"""Background scheduler thread.

Responsibilities:
- Recalculate prayer times once per day (at startup and at midnight).
- Poll every second to update the countdown and current time in :class:`AppState`.
- Recalculate immediately when :meth:`Scheduler.update_components` is called
  (triggered by config hot-reload).
"""

import logging
import threading
import zoneinfo
from datetime import date, datetime, timedelta
from typing import Optional

from app_state import AppState
from config.models import AppConfig
from prayer.calculator import COUNTDOWN_PRAYERS, IQAMAH_PRAYERS, Location, PrayerCalculator, PrayerTimes
from prayer.iqamah import IqamahEngine

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        calculator: PrayerCalculator,
        iqamah_engine: IqamahEngine,
        state: AppState,
        config: AppConfig,
    ) -> None:
        self._calculator = calculator
        self._iqamah_engine = iqamah_engine
        self._state = state
        self._config = config
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="scheduler")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._recalculate()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def update_components(
        self,
        calculator: PrayerCalculator,
        iqamah_engine: IqamahEngine,
        config: AppConfig,
    ) -> None:
        """Replace inner components and immediately recalculate.

        Called from the watchdog thread on config hot-reload.  Thread-safe
        because attribute assignment is atomic in CPython and ``_recalculate``
        only reads these after assignment.
        """
        self._calculator = calculator
        self._iqamah_engine = iqamah_engine
        self._config = config
        self._recalculate()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        last_date: Optional[date] = None
        while not self._stop.is_set():
            tz = zoneinfo.ZoneInfo(self._config.location.timezone)
            now = datetime.now(tz=tz)
            today = now.date()

            if today != last_date:
                self._recalculate()
                last_date = today

            self._tick(now)
            self._stop.wait(1.0)

    # ------------------------------------------------------------------
    # Calculation helpers
    # ------------------------------------------------------------------

    def _recalculate(self) -> None:
        cfg = self._config
        loc = Location(
            latitude=cfg.location.latitude,
            longitude=cfg.location.longitude,
            timezone=cfg.location.timezone,
            elevation=cfg.location.elevation,
        )
        try:
            today = date.today()
            prayer_times = self._calculator.get_times(today, loc)
            iqamah_times = self._iqamah_engine.compute(prayer_times)
            hijri = self._get_hijri(today) if cfg.hijri.enabled else (0, 0, 0)
            self._state.write(
                prayer_times=prayer_times,
                iqamah_times=iqamah_times,
                hijri=hijri,
            )
            logger.info("Prayer times recalculated for %s", today)
        except Exception:
            logger.exception("Failed to calculate prayer times")

    def _tick(self, now: datetime) -> None:
        snap = self._state.snapshot()
        pt = snap.prayer_times
        if pt is None:
            self._state.write(current_time=now)
            return

        tz = zoneinfo.ZoneInfo(self._config.location.timezone)

        # Jumu'ah: fixed-time Friday congregation replacing Dhuhr in the countdown
        jumuah_cfg = self._config.jumuah
        jumuah_time: Optional[datetime] = None
        if jumuah_cfg.enabled and now.weekday() == 4:  # 4 = Friday
            jumuah_time = now.replace(
                hour=jumuah_cfg.hour, minute=jumuah_cfg.minute, second=0, microsecond=0
            )

        upcoming: list[tuple[str, datetime]] = []
        for name in COUNTDOWN_PRAYERS:
            adhan_time = getattr(pt, name)
            if adhan_time.tzinfo is None:
                adhan_time = adhan_time.replace(tzinfo=tz)
            # On Fridays use Jumu'ah time instead of calculated Dhuhr for countdown
            if name == "dhuhr" and jumuah_time is not None:
                adhan_time = jumuah_time
            if adhan_time > now:
                upcoming.append((name, adhan_time))

        if upcoming:
            next_name, next_adhan = upcoming[0]
        else:
            # All today's prayers have passed — count down to tomorrow's Fajr
            next_name = "fajr"
            next_adhan = self._get_tomorrow_fajr(tz)

        next_iqamah = snap.iqamah_times.get(next_name)

        # Compute progress through the current inter-prayer interval (0.0 → 1.0)
        past = [
            getattr(pt, n)
            for n in COUNTDOWN_PRAYERS
            if getattr(pt, n) <= now
        ]
        if past:
            interval_start = max(past)  # most recent past prayer
        else:
            # Before today's Fajr — approximate start as yesterday's Isha
            interval_start = getattr(pt, "isha") - timedelta(days=1)
        if interval_start.tzinfo is None:
            interval_start = interval_start.replace(tzinfo=tz)
        total_secs = (next_adhan - interval_start).total_seconds()
        elapsed_secs = (now - interval_start).total_seconds()
        interval_progress = (
            max(0.0, min(1.0, elapsed_secs / total_secs)) if total_secs > 0 else 0.0
        )

        # Detect if now is between a prayer's adhan and its iqamah
        current_iqamah: Optional[datetime] = None
        current_iqamah_name: str = ""
        current_iqamah_progress: float = 0.0
        for name in IQAMAH_PRAYERS:
            adhan_dt = getattr(pt, name)
            if adhan_dt.tzinfo is None:
                adhan_dt = adhan_dt.replace(tzinfo=tz)
            if name == "dhuhr" and jumuah_time is not None:
                adhan_dt = jumuah_time
            iq_dt = snap.iqamah_times.get(name)
            if iq_dt is not None:
                if iq_dt.tzinfo is None:
                    iq_dt = iq_dt.replace(tzinfo=tz)
                if adhan_dt <= now < iq_dt:
                    current_iqamah_name = name
                    current_iqamah = iq_dt
                    span = (iq_dt - adhan_dt).total_seconds()
                    elapsed_iq = (now - adhan_dt).total_seconds()
                    current_iqamah_progress = (
                        max(0.0, min(1.0, elapsed_iq / span)) if span > 0 else 0.0
                    )
                    break

        self._state.write(
            current_time=now,
            next_prayer_name=next_name,
            next_prayer_adhan=next_adhan,
            next_prayer_iqamah=next_iqamah,
            countdown=next_adhan - now,
            interval_progress=interval_progress,
            jumuah_time=jumuah_time,
            current_iqamah=current_iqamah,
            current_iqamah_name=current_iqamah_name,
            current_iqamah_progress=current_iqamah_progress,
        )

    def _get_tomorrow_fajr(self, tz: zoneinfo.ZoneInfo) -> datetime:
        tomorrow = date.today() + timedelta(days=1)
        cfg = self._config
        loc = Location(
            latitude=cfg.location.latitude,
            longitude=cfg.location.longitude,
            timezone=cfg.location.timezone,
            elevation=cfg.location.elevation,
        )
        try:
            pt = self._calculator.get_times(tomorrow, loc)
            fajr = pt.fajr
            if fajr.tzinfo is None:
                fajr = fajr.replace(tzinfo=tz)
            return fajr
        except Exception:
            logger.exception("Failed to get tomorrow's Fajr; using +24h estimate")
            snap = self._state.snapshot()
            if snap.prayer_times:
                return snap.prayer_times.fajr + timedelta(days=1)
            return datetime.now(tz=tz) + timedelta(hours=24)

    @staticmethod
    def _get_hijri(today: date) -> tuple[int, int, int]:
        try:
            from hijri_converter import convert

            h = convert.Gregorian(today.year, today.month, today.day).to_hijri()
            return (h.year, h.month, h.day)
        except Exception:
            logger.warning("Hijri conversion failed", exc_info=True)
            return (0, 0, 0)

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass
class Location:
    latitude: float
    longitude: float
    timezone: str
    elevation: float = 0.0


@dataclass
class PrayerTimes:
    fajr: datetime
    sunrise: datetime
    dhuhr: datetime
    asr: datetime
    maghrib: datetime
    isha: datetime
    tahajjud: datetime  # start of the last third of the night (Maghrib → next Fajr)
    date: date

    def as_dict(self) -> dict[str, datetime]:
        """Return ordered mapping of prayer name → adhan time."""
        return {
            "fajr": self.fajr,
            "sunrise": self.sunrise,
            "dhuhr": self.dhuhr,
            "asr": self.asr,
            "maghrib": self.maghrib,
            "isha": self.isha,
            "tahajjud": self.tahajjud,
        }


# Prayers that have an iqamah (sunrise and midnight do not).
IQAMAH_PRAYERS = ("fajr", "dhuhr", "asr", "maghrib", "isha")

# Ordered list used for "next prayer" countdown logic.
COUNTDOWN_PRAYERS = ("fajr", "dhuhr", "asr", "maghrib", "isha")


class PrayerCalculator(ABC):
    """Abstract strategy for calculating daily prayer times."""

    @abstractmethod
    def get_times(self, for_date: date, location: Location) -> PrayerTimes:
        ...


# ---------------------------------------------------------------------------
# Config method name → adhanpy CalculationMethod enum attribute name
# ---------------------------------------------------------------------------
_METHOD_MAP: dict[str, str] = {
    "ISNA":          "NORTH_AMERICA",
    "NorthAmerica":  "NORTH_AMERICA",
    "MWL":           "MUSLIM_WORLD_LEAGUE",
    "Egyptian":      "EGYPTIAN",
    "Karachi":       "KARACHI",
    "UmmAlQura":     "UMM_AL_QURA",
    "Dubai":         "DUBAI",
    "Qatar":         "QATAR",
    "Kuwait":        "KUWAIT",
    "MoonSighting":  "MOON_SIGHTING_COMMITTEE",
    "Singapore":     "SINGAPORE",
    "Tehran":        "TEHRAN",
}


class AdhanCalculator(PrayerCalculator):
    """Wraps ``adhanpy`` (Python port of batoulapps/adhan-java) to implement
    :class:`PrayerCalculator`.

    Accuracy is based on Jean Meeus's "Astronomical Algorithms", the same
    reference used by NOAA and the US Naval Observatory.
    """

    def __init__(self, method: str, asr_madhab: str) -> None:
        self._method = method
        self._asr_madhab = asr_madhab

    def get_times(self, for_date: date, location: Location) -> PrayerTimes:
        import zoneinfo
        from adhanpy.calculation.CalculationMethod import CalculationMethod
        from adhanpy.PrayerTimes import PrayerTimes as _APT

        tz = zoneinfo.ZoneInfo(location.timezone)
        coords = (location.latitude, location.longitude)

        method_enum = getattr(
            CalculationMethod,
            _METHOD_MAP.get(self._method, "NORTH_AMERICA"),
            CalculationMethod.NORTH_AMERICA,
        )
        params = self._build_params(method_enum)

        apt = _APT(coords, for_date, calculation_parameters=params, time_zone=tz)

        return PrayerTimes(
            fajr=_tz(apt.fajr, tz),
            sunrise=_tz(apt.sunrise, tz),
            dhuhr=_tz(apt.dhuhr, tz),
            asr=_tz(apt.asr, tz),
            maghrib=_tz(apt.maghrib, tz),
            isha=_tz(apt.isha, tz),
            tahajjud=self._tahajjud(apt, coords, params, for_date, _APT, tz),
            date=for_date,
        )

    # ------------------------------------------------------------------

    def _build_params(self, method_enum):
        """Return CalculationParameters for the chosen method + madhab.

        adhanpy API: CalculationParameters(method=<enum>) creates params with
        the correct angles for that method. Set .madhab for Asr calculation.
        """
        from adhanpy.calculation.CalculationParameters import CalculationParameters
        from adhanpy.calculation.Madhab import Madhab

        params = CalculationParameters(method=method_enum)
        params.madhab = Madhab.HANAFI if self._asr_madhab.lower() == "hanafi" else Madhab.SHAFI
        return params

    def _tahajjud(self, apt, coords, params, for_date: date, _APT, tz):
        """Return the start of the last third of the night.

        Night = Maghrib (today) → Fajr (tomorrow).
        Last third begins at: Maghrib + 2/3 * night_duration.
        """
        tomorrow = for_date + timedelta(days=1)
        try:
            next_apt = _APT(coords, tomorrow, calculation_parameters=params, time_zone=tz)
            fajr_tomorrow = _tz(next_apt.fajr, tz)
        except Exception:
            fajr_tomorrow = _tz(apt.isha, tz) + timedelta(hours=3)
        maghrib = _tz(apt.maghrib, tz)
        night = fajr_tomorrow - maghrib
        return maghrib + (night * 2 / 3)


def _tz(dt: datetime, tz) -> datetime:
    """Ensure datetime carries the local timezone (defensive — adhanpy usually does this)."""
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)

"""Iqamah time computation.

:class:`IqamahEngine` takes a set of per-prayer :class:`~prayer.rules.IqamahRule`
objects and applies them to a :class:`~prayer.calculator.PrayerTimes` instance
to produce the iqamah times for each prayer.

Sunrise and Tahajjud are display-only times and have no iqamah — they always
map to ``None`` in the result.
"""

from datetime import datetime
from typing import Optional

from config.models import IqamahConfig
from prayer.calculator import IQAMAH_PRAYERS, PrayerTimes
from prayer.rules import IqamahRule, build_rule

# Prayers that have no iqamah; always None in the output dict.
_NO_IQAMAH = frozenset({"sunrise", "tahajjud"})


class IqamahEngine:
    """Apply configured rules to adhan times to produce iqamah times."""

    def __init__(self, rules: dict[str, list[IqamahRule]]) -> None:
        # rules: prayer name → ordered list of rules to apply in sequence
        self._rules = rules

    def compute(self, prayer_times: PrayerTimes) -> dict[str, Optional[datetime]]:
        """Return a mapping of prayer name → iqamah datetime (or None)."""
        result: dict[str, Optional[datetime]] = {}
        for name, adhan_time in prayer_times.as_dict().items():
            if name in _NO_IQAMAH:
                result[name] = None
                continue
            iqamah_time = adhan_time
            for rule in self._rules.get(name, []):
                iqamah_time = rule.apply(iqamah_time)
            result[name] = iqamah_time
        return result


def build_iqamah_engine(config: IqamahConfig) -> IqamahEngine:
    """Build an :class:`IqamahEngine` from a validated config object."""
    rules: dict[str, list[IqamahRule]] = {}
    for prayer in IQAMAH_PRAYERS:
        rule_configs = getattr(config, prayer, [])
        rules[prayer] = [build_rule(rc) for rc in rule_configs]
    return IqamahEngine(rules)

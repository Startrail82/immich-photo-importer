"""Standaard 5-veld cron-expressies (minuut uur dag-van-maand maand dag-van-week), alleen stdlib.

Ondersteund per veld: * , - / en getallen; maanden (jan..dec) en weekdagen (zon..zat, ma..zo)
ook met naam. Weekdag 0 en 7 zijn beide zondag. Zoals in klassiek cron geldt: zijn zowel
dag-van-maand als weekdag beperkt, dan volstaat dat een van beide klopt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_DOW = {d: i for i, d in enumerate("sun mon tue wed thu fri sat".split())}
_DOW.update({"zon": 0, "maa": 1, "din": 2, "woe": 3, "don": 4, "vri": 5, "zat": 6})
_DOW.update({"zo": 0, "ma": 1, "di": 2, "wo": 3, "do": 4, "vr": 5, "za": 6})
_DAYS_NL = ["zondag", "maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag"]


class CronError(ValueError):
    pass


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = zondag
    days_any: bool
    weekdays_any: bool
    expression: str

    def matches_day(self, day: datetime) -> bool:
        if day.month not in self.months:
            return False
        dom_ok = day.day in self.days
        dow_ok = ((day.weekday() + 1) % 7) in self.weekdays
        if self.days_any and self.weekdays_any:
            return True
        if self.days_any:
            return dow_ok
        if self.weekdays_any:
            return dom_ok
        return dom_ok or dow_ok

    def next_after(self, now: datetime) -> datetime:
        """Eerstvolgende tijdstip strikt na 'now' (op de minuut nauwkeurig)."""
        start = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        day = start.replace(hour=0, minute=0)
        for _ in range(366 * 5):
            if self.matches_day(day):
                for hour in sorted(self.hours):
                    for minute in sorted(self.minutes):
                        candidate = day.replace(hour=hour, minute=minute)
                        if candidate >= start:
                            return candidate
            day += timedelta(days=1)
        raise CronError("deze cron-expressie komt de komende jaren nooit voor")

    def upcoming(self, now: datetime, count: int = 3) -> list[datetime]:
        out: list[datetime] = []
        cursor = now
        for _ in range(count):
            cursor = self.next_after(cursor)
            out.append(cursor)
        return out


def _field(text: str, low: int, high: int, names: dict[str, int] | None, label: str) -> tuple[frozenset[int], bool]:
    values: set[int] = set()
    is_any = text.startswith("*")  # klassiek cron: een veld dat met * begint telt als 'elke'

    def number(token: str) -> int:
        token = token.lower()
        if names and token in names:
            return names[token]
        if names and len(token) > 3 and token[:3] in names and token.isalpha():
            return names[token[:3]]
        if not token.isdigit():
            raise CronError(f"{label}: ongeldige waarde {token!r}")
        return int(token)

    for part in text.split(","):
        if not part:
            raise CronError(f"{label}: lege waarde")
        step = 1
        rng = part
        if "/" in part:
            rng, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError(f"{label}: ongeldige stap {step_text!r}")
            step = int(step_text)
        if rng == "*":
            a, b = low, high
        elif "-" in rng:
            left, _, right = rng.partition("-")
            a, b = number(left), number(right)
        else:
            a = number(rng)
            b = high if "/" in part else a
        if a < low or b > high or a > b:
            raise CronError(f"{label}: waarde buiten bereik ({low}-{high}): {part!r}")
        values.update(range(a, b + 1, step))
    if label == "weekdag" and 7 in values:
        values.discard(7)
        values.add(0)
    return frozenset(values), is_any


def parse(expression: str) -> Cron:
    parts = expression.split()
    if len(parts) != 5:
        raise CronError("een cron-expressie heeft 5 velden: minuut uur dag maand weekdag (bv. 0 12 * * *)")
    minutes, _ = _field(parts[0], 0, 59, None, "minuut")
    hours, _ = _field(parts[1], 0, 23, None, "uur")
    days, days_any = _field(parts[2], 1, 31, None, "dag")
    months, _ = _field(parts[3], 1, 12, _MONTHS, "maand")
    weekdays, weekdays_any = _field(parts[4], 0, 7, _DOW, "weekdag")
    return Cron(minutes, hours, days, months, weekdays, days_any, weekdays_any, " ".join(parts))


def describe(expression: str) -> str:
    """Korte uitleg in het Nederlands voor de gangbare vormen; anders een neutrale zin."""
    cron = parse(expression)
    p = cron.expression.split()
    minute_p, hour_p, dom_p, mon_p, dow_p = p
    when = None
    if minute_p.isdigit() and hour_p.isdigit():
        when = f"om {int(hour_p):02d}:{int(minute_p):02d}"
    elif minute_p.isdigit() and hour_p == "*":
        when = f"elk uur op minuut {int(minute_p)}" if int(minute_p) else "elk uur"
    elif minute_p.isdigit() and hour_p.startswith("*/") and hour_p[2:].isdigit():
        when = f"elke {int(hour_p[2:])} uur (op minuut {int(minute_p)})"
    elif minute_p.startswith("*/") and minute_p[2:].isdigit() and hour_p == "*":
        when = f"elke {int(minute_p[2:])} minuten"
    if when is None or dom_p != "*" or mon_p != "*":
        return "volgens de ingestelde cron-expressie (zie de eerstvolgende uitvoeringen)"
    if dow_p == "*":
        return ("elke dag " + when) if when.startswith("om ") else when
    days = sorted(cron.weekdays, key=lambda d: (d + 6) % 7)  # maandag eerst
    names = [_DAYS_NL[d] for d in days]
    if len(names) == 1:
        label = f"elke {names[0]}"
    elif days == [1, 2, 3, 4, 5]:
        label = "elke werkdag (ma-vr)"
    else:
        label = "op " + ", ".join(names[:-1]) + " en " + names[-1]
    return f"{label} {when}"

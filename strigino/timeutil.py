"""Работа со временем.

Табло аэропорта отдаёт время в местной зоне Нижнего Новгорода — это MSK
(UTC+3, без перехода на летнее время). Смещение фиксированное, поэтому
tzdata (которой на OpenWrt может не быть) не требуется.

Внутри всё хранится в UTC (ISO 8601), наружу выводится в MSK.
"""

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3), "MSK")

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_msk() -> datetime:
    return datetime.now(MSK)


def to_msk(dt: datetime) -> datetime:
    return dt.astimezone(MSK)


def iso(dt: datetime) -> str:
    """UTC ISO-строка для хранения в базе."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value):
    if not value:
        return None
    return datetime.fromisoformat(value)


def parse_board_datetime(day_month: str, hhmm: str, reference: datetime = None) -> datetime:
    """Собрать дату из табло: "08.09" + "00:30" -> datetime в MSK.

    Год на табло не указан, поэтому берётся ближайший к `reference`:
    это корректно отрабатывает переход через Новый год в обе стороны.
    """
    day_month = (day_month or "").strip()
    hhmm = (hhmm or "").strip()
    if not day_month or not hhmm:
        return None

    try:
        day, month = (int(x) for x in day_month.split(".")[:2])
        hour, minute = (int(x) for x in hhmm.split(":")[:2])
    except (ValueError, TypeError):
        return None

    ref = reference or now_msk()
    best = None
    for year in (ref.year - 1, ref.year, ref.year + 1):
        try:
            candidate = datetime(year, month, day, hour, minute, tzinfo=MSK)
        except ValueError:
            continue  # 29 февраля в невисокосном году
        if best is None or abs(candidate - ref) < abs(best - ref):
            best = candidate
    return best


def fmt_ru(dt: datetime) -> str:
    """"9 сентября 20:50" — формат из ТЗ."""
    if dt is None:
        return "—"
    dt = to_msk(dt)
    return f"{dt.day} {MONTHS_RU[dt.month]} {dt:%H:%M}"


def fmt_ru_time(dt: datetime) -> str:
    """"00:10" — только время, для фразы "10 сентября в 00:10"."""
    return f"{to_msk(dt):%H:%M}"


def fmt_ru_date(dt: datetime) -> str:
    """"10 сентября" — только дата."""
    dt = to_msk(dt)
    return f"{dt.day} {MONTHS_RU[dt.month]}"


def human_minutes(minutes: int) -> str:
    """90 -> "1 ч 30 мин"."""
    minutes = int(minutes)
    sign = "-" if minutes < 0 else ""
    minutes = abs(minutes)
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{sign}{hours} ч {mins} мин"
    if hours:
        return f"{sign}{hours} ч"
    return f"{sign}{mins} мин"

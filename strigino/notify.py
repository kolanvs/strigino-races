"""Формирование текстов оповещений.

Сообщения размечены HTML-разметкой Telegram (parse_mode=HTML), поэтому всё,
что приходит с сайта — номера рейсов, названия городов — обязательно
экранируется: иначе случайный `<` или `&` сломает разбор и Telegram
отклонит сообщение целиком.

Дата у времени печатается только тогда, когда отличается от даты вылета по
расписанию. В сообщении о задержке все времена почти всегда относятся к
одному дню, и повторять «9 сентября» четыре раза — только мешать чтению.
А вот вылет, уехавший за полночь, дату получит.
"""

from html import escape

from .board import ORIGIN_IATA, ORIGIN_NAME
from .timeutil import (fmt_ru, fmt_ru_date, fmt_ru_time, human_minutes,
                       parse_iso, to_msk)


def _same_day(first, second):
    return first and second and to_msk(first).date() == to_msk(second).date()


def _time(moment, reference=None):
    """Время без даты, если тот же день, что и reference; иначе с датой."""
    if reference is not None and not _same_day(moment, reference):
        return fmt_ru(moment)
    return fmt_ru_time(moment)


def _parts(flight):
    """Составляющие рейса: номер, откуда, куда, коды аэропортов.

    Собирается из отдельных полей, а не разбором готовой строки маршрута:
    в названиях городов встречается дефис («Санкт-Петербург»), и делить
    маршрут по нему было бы миной замедленного действия.
    """
    destination = escape(flight["dest_name"] or "")
    iata = escape(flight["dest_iata"] or "")

    if flight["kind"] == "arr":
        origin, target = destination, ORIGIN_NAME
        codes = "%s-%s" % (iata, ORIGIN_IATA)
    else:
        origin, target = ORIGIN_NAME, destination
        codes = "%s-%s" % (ORIGIN_IATA, iata)

    return {
        "number": escape(flight["flight_no"] or ""),
        "origin": origin,
        "target": target,
        "codes": codes.strip("-"),
    }


def _flight_block(flight, icon, action, include_flight_number=True, suffix=""):
    """Шапка из двух строк: что случилось с рейсом и куда он летит.

    Раздельно, потому что одной строкой «Задержан рейс SU-800 Нижний
    Новгород → Анталья» получается длинно и на телефоне переносится
    в произвольном месте.

    Номер рейса нужен: в один день бывает несколько рейсов по одному
    направлению — в Анталью летают и U6-1579, и SU-800, и ZF-443.
    """
    parts = _parts(flight)
    title = action
    if include_flight_number and parts["number"]:
        title = "%s %s" % (action, parts["number"])

    lines = ["%s <b>%s</b>%s" % (icon, title, suffix)]
    route = "%s → %s" % (parts["origin"], parts["target"])
    if parts["codes"]:
        route += " · <code>%s</code>" % parts["codes"]
    lines.append(route)
    return lines


def delay_message(flight, delays, include_flight_number=True, show_total=True):
    """Оповещение о задержке. Повторная задержка добавляет историю."""
    current = delays[-1]
    previous = delays[:-1]

    scheduled = parse_iso(flight["scheduled_utc"])
    new_time = parse_iso(current["new_expected_utc"])
    detected = parse_iso(current["detected_utc"])

    suffix = " · задержка №%d" % len(delays) if previous else ""
    lines = _flight_block(flight, "⚠️", "Задержан рейс",
                          include_flight_number, suffix)

    lines.append("")
    lines.append("По расписанию: <b>%s</b>" % fmt_ru(scheduled))
    lines.append("Новое время: <b>%s</b>" % _time(new_time, scheduled))
    if show_total:
        lines.append("Задержка: <b>%s</b>" % human_minutes(current["delay_minutes"]))

    if previous:
        lines.append("")
        lines.append("Объявляли ранее:")
        for delay in previous:
            lines.append("• %s — обнаружено %s" % (
                _time(parse_iso(delay["new_expected_utc"]), scheduled),
                _time(parse_iso(delay["detected_utc"]), scheduled)))

    lines.append("")
    lines.append("<i>Обнаружено %s</i>" % _time(detected, scheduled))
    return "\n".join(lines)


def departure_message(flight, delays, include_flight_number=True, show_total=True):
    """Оповещение о вылете задержанного рейса — с историей времени."""
    scheduled = parse_iso(flight["scheduled_utc"])
    actual = parse_iso(flight["expected_utc"])

    lines = _flight_block(flight, "\U0001f6eb", "Вылетел рейс", include_flight_number)

    lines.append("")
    lines.append("Фактический вылет: <b>%s</b>" % fmt_ru(actual))
    if show_total:
        total = int((actual - scheduled).total_seconds() // 60)
        lines.append("Итоговая задержка: <b>%s</b>" % human_minutes(total))

    lines.append("")
    lines.append("История:")
    lines.append("• по расписанию — %s" % _time(scheduled, scheduled))
    for delay in delays:
        lines.append("• %s — обнаружено %s" % (
            _time(parse_iso(delay["new_expected_utc"]), scheduled),
            _time(parse_iso(delay["detected_utc"]), scheduled)))
    return "\n".join(lines)


def cancelled_message(flight, include_flight_number=True):
    """Рейс сняли с табло как отменённый."""
    lines = _flight_block(flight, "\U0001f6ab", "Отменён рейс", include_flight_number)
    lines.append("")
    lines.append("Вылет по расписанию: <b>%s</b>"
                 % fmt_ru(parse_iso(flight["scheduled_utc"])))
    return "\n".join(lines)


def flights_message(rows, include_flight_number=True):
    """Ответ на /flights — ближайшие вылеты, сгруппированные по дням.

    Дата выносится в заголовок группы, а не повторяется в каждой строке:
    в списке из двух десятков рейсов это половина текста.
    """
    if not rows:
        return "\U0001f4cb <b>Ближайшие вылеты</b>\n\nНа табло нет предстоящих рейсов."

    lines = ["\U0001f4cb <b>Ближайшие вылеты</b>"]
    current_day = None

    for row in rows:
        scheduled = parse_iso(row["scheduled_utc"])
        expected = parse_iso(row["expected_utc"])
        day = to_msk(scheduled).date()

        if day != current_day:
            current_day = day
            lines.append("")
            lines.append("<b>%s</b>" % fmt_ru_date(scheduled))

        parts = _parts(row)
        # Пункт отправления в списке не печатается: он у всех строк один
        # и тот же, а место на экране телефона не бесконечное.
        title = "%s → %s" % (parts["number"], parts["target"]) \
            if (include_flight_number and parts["number"]) else parts["target"]

        minutes = int((expected - scheduled).total_seconds() // 60)
        if minutes > 0:
            mark = "  ⚠️ <b>%s</b> (+%s)" % (
                _time(expected, scheduled), human_minutes(minutes))
        else:
            mark = ""
        lines.append("%s · %s%s" % (fmt_ru_time(scheduled), title, mark))

    return "\n".join(lines)


def status_message(interval_minutes, threshold, stats, last_poll, last_error=None):
    """Ответ на /status."""
    lines = [
        "\U0001f4ca <b>Мониторинг табло Стригино</b>",
        "",
        "Период опроса: <b>%d мин</b>" % interval_minutes,
        "Порог задержки: <b>%d мин</b>" % threshold,
        "Последний опрос: <b>%s</b>" % (fmt_ru(last_poll) if last_poll else "ещё не было"),
        "",
        "Рейсов в базе: %d" % stats["flights"],
        "Задержек записано: %d" % stats["delays"],
        "Сообщений отправлено: %d, в очереди: %d" % (stats["sent"], stats["pending"]),
        "Активных чатов: %d" % stats["chats"],
    ]
    if last_error:
        lines.append("")
        lines.append("⚠️ Последняя ошибка: <i>%s</i>" % escape(str(last_error)))
    return "\n".join(lines)


HELP_TEXT = (
    "✈️ <b>Задержки вылетов из Стригино</b>\n"
    "\n"
    "Слежу за онлайн-табло аэропорта и сообщаю, когда рейс задерживают "
    "или когда задержанный рейс наконец вылетает.\n"
    "\n"
    "Пришлите <b>число от 2 до 30</b> — это период проверки табло в минутах.\n"
    "\n"
    "<b>Команды</b>\n"
    "/status — настройки и состояние\n"
    "/flights — ближайшие вылеты и задержки\n"
    "/threshold N — оповещать о задержках от N минут\n"
    "/help — эта справка\n"
    "/stop — отписаться от оповещений"
)

SUBSCRIBED_TEXT = (
    "✅ <b>Подписка оформлена.</b> Оповещения о задержках будут приходить сюда.\n"
    "\n" + HELP_TEXT
)

"""Формирование текстов оповещений.

Формулировки заданы техническим заданием и воспроизводятся дословно;
меняется только подстановка данных.
"""

from .timeutil import fmt_ru, fmt_ru_date, fmt_ru_time, human_minutes, parse_iso


def _flight_label(flight_no, route, include_flight_number=True):
    """"SU-800 Нижний Новгород-Анталья (GOJ-AYT)".

    Номер рейса нужен, потому что в один день бывает несколько рейсов по
    одному направлению (в Анталью летают и U6-1579, и SU-800, и ZF-443),
    и без номера сообщения не различить. Отключается настройкой
    include_flight_number.
    """
    if include_flight_number and flight_no:
        return "%s %s" % (flight_no, route)
    return route


def delay_message(flight, delays, include_flight_number=True, show_total=True):
    """Оповещение о задержке — форматы 1 и 2 из ТЗ.

    ``delays`` — все записи задержек рейса по порядку; последняя считается
    только что обнаруженной.
    """
    current = delays[-1]
    previous = delays[:-1]

    label = _flight_label(flight["flight_no"], flight["route"], include_flight_number)
    text = (
        "Задержан рейс %s, заявленное время вылета - %s, новое время вылета - %s, "
        "время обнаружения задержки - %s."
        % (
            label,
            fmt_ru(parse_iso(flight["scheduled_utc"])),
            fmt_ru(parse_iso(current["new_expected_utc"])),
            fmt_ru(parse_iso(current["detected_utc"])),
        )
    )

    if previous:
        announced = ", ".join(fmt_ru(parse_iso(d["new_expected_utc"])) for d in previous)
        detected = ", ".join(fmt_ru(parse_iso(d["detected_utc"])) for d in previous)
        text += (" Ранее объявленные времена вылета: %s."
                 " Времена обнаружения задержек: %s" % (announced, detected))

    if show_total:
        text += "\nОбщая задержка: %s." % human_minutes(current["delay_minutes"])
    return text


def departure_message(flight, delays, include_flight_number=True, show_total=True):
    """Оповещение о вылете задержанного рейса — формат 3 из ТЗ."""
    label = _flight_label(flight["flight_no"], flight["route"], include_flight_number)
    actual = parse_iso(flight["expected_utc"])
    scheduled = parse_iso(flight["scheduled_utc"])

    lines = [
        "Вылетел рейс %s %s в %s. История времени рейса:"
        % (label, fmt_ru_date(actual), fmt_ru_time(actual)),
        "Изначальное время: %s" % fmt_ru(scheduled),
    ]
    for delay in delays:
        lines.append(
            "Задержка - %s, время обнаружения - %s"
            % (fmt_ru(parse_iso(delay["new_expected_utc"])),
               fmt_ru(parse_iso(delay["detected_utc"])))
        )

    if show_total:
        total = int((actual - scheduled).total_seconds() // 60)
        lines.append("Итоговая задержка: %s." % human_minutes(total))
    return "\n".join(lines)


def cancelled_message(flight, include_flight_number=True):
    """Рейс сняли с табло как отменённый."""
    label = _flight_label(flight["flight_no"], flight["route"], include_flight_number)
    return ("Отменён рейс %s, время вылета по расписанию - %s."
            % (label, fmt_ru(parse_iso(flight["scheduled_utc"]))))


def status_message(interval_minutes, threshold, stats, last_poll, last_error=None):
    """Ответ на команду /status."""
    lines = [
        "Мониторинг табло вылетов Стригино (GOJ)",
        "Период опроса: %d мин." % interval_minutes,
        "Порог задержки: %d мин." % threshold,
        "Последний опрос: %s" % (fmt_ru(last_poll) if last_poll else "ещё не было"),
        "Рейсов в базе: %d, задержек: %d" % (stats["flights"], stats["delays"]),
        "Сообщений в очереди: %d, отправлено: %d" % (stats["pending"], stats["sent"]),
        "Активных чатов: %d" % stats["chats"],
    ]
    if last_error:
        lines.append("Последняя ошибка: %s" % last_error)
    return "\n".join(lines)


def flights_message(rows, include_flight_number=True):
    """Ответ на команду /flights — ближайшие рейсы и их задержки."""
    if not rows:
        return "На табло нет предстоящих вылетов."

    lines = ["Ближайшие вылеты:"]
    for row in rows:
        scheduled = parse_iso(row["scheduled_utc"])
        expected = parse_iso(row["expected_utc"])
        label = _flight_label(row["flight_no"], row["route"], include_flight_number)
        minutes = int((expected - scheduled).total_seconds() // 60)
        if minutes > 0:
            suffix = " -> %s (задержка %s)" % (fmt_ru_time(expected), human_minutes(minutes))
        else:
            suffix = " — по расписанию"
        lines.append("%s: %s%s" % (fmt_ru(scheduled), label, suffix))
    return "\n".join(lines)


HELP_TEXT = (
    "Бот следит за табло вылетов аэропорта Стригино и сообщает о задержках.\n"
    "\n"
    "Пришлите число от 2 до 30 — это период проверки табло в минутах.\n"
    "\n"
    "Команды:\n"
    "/status — текущие настройки и состояние\n"
    "/flights — ближайшие вылеты и задержки\n"
    "/threshold N — минимальная задержка для оповещения, мин.\n"
    "/help — эта справка\n"
    "/stop — отписаться от оповещений"
)

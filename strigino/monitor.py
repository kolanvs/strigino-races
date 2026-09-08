"""Сравнение табло с базой и постановка оповещений в очередь.

Ключевое правило: задержкой считается изменение ожидаемого времени у
рейса, который **ещё не вылетел**. У уже вылетевшего рейса та же колонка
означает фактическое время отправления, и его отличие от расписания на
несколько минут — обычное дело, а не объявленная задержка. Поэтому такие
рейсы обрабатываются отдельной веткой (сообщение о вылете).
"""

import logging

from . import notify
from .board import BoardError, fetch_departures
from .timeutil import iso, now_utc, parse_iso

log = logging.getLogger("strigino.monitor")

SEEDED_KEY = "seeded"
LAST_POLL_KEY = "last_poll_utc"
LAST_CLEANUP_KEY = "last_cleanup_utc"


class Monitor:
    def __init__(self, db, config):
        self.db = db
        self.config = config
        self.last_error = None

    # -- настройки, которые можно менять из бота -------------------------

    @property
    def threshold(self):
        return self.db.get_int_setting("min_delay_minutes",
                                       self.config.min_delay_minutes)

    @property
    def departed_threshold(self):
        return self.db.get_int_setting("departed_min_delay_minutes",
                                       self.config.departed_min_delay_minutes)

    @property
    def interval_minutes(self):
        return self.db.get_int_setting("poll_interval_minutes",
                                       self.config.poll_interval_minutes)

    # -- основной проход -------------------------------------------------

    def poll(self):
        """Один цикл: скачать табло, сравнить, поставить оповещения в очередь.

        Возвращает словарь со статистикой цикла.
        """
        try:
            rows = fetch_departures(
                timeout=self.config.http_timeout,
                retries=self.config.http_retries,
                include_tomorrow=self.config.include_tomorrow,
                user_agent=self.config.user_agent,
            )
        except BoardError as exc:
            self.last_error = str(exc)
            log.error("не удалось получить табло: %s", exc)
            return {"error": str(exc)}

        seeding = self.db.get_setting(SEEDED_KEY) != "1"
        if seeding:
            log.info("первый запуск: заполняю базу без оповещений (%d рейсов)", len(rows))

        result = {"rows": len(rows), "delays": 0, "departures": 0,
                  "cancelled": 0, "new": 0, "seeding": seeding}

        for row in rows:
            try:
                self._process(row, seeding, result)
            except Exception:  # один битый рейс не должен ронять цикл
                log.exception("ошибка обработки рейса %s", row.leg_key)

        self.db.set_setting(SEEDED_KEY, "1")
        self.db.set_setting(LAST_POLL_KEY, iso(now_utc()))
        self.last_error = None
        log.info("опрос: %s", result)
        return result

    def _process(self, row, seeding, result):
        previous = self.db.get_flight(row.leg_key)
        detected = now_utc()

        if previous is None:
            result["new"] += 1
            self.db.upsert_flight(row)
            # Рейс может появиться на табло уже с объявленной задержкой.
            if (not row.is_departed and not row.is_cancelled
                    and row.delay_minutes >= self.threshold):
                self._record_delay(row, None, detected, seeding, result)
            return

        previous_expected = parse_iso(previous["expected_utc"])
        was_departed = bool(previous["departed"])
        was_cancelled = bool(previous["cancelled"])

        # 1. Новое объявленное время вылета у ещё не улетевшего рейса.
        if (not row.is_departed and not row.is_cancelled
                and row.expected > previous_expected
                and row.delay_minutes >= self.threshold):
            self._record_delay(row, previous_expected, detected, seeding, result)

        self.db.upsert_flight(row)
        flight = self.db.get_flight(row.leg_key)

        # 2. Рейс вылетел.
        if row.is_departed and not was_departed and not previous["departed_notified"]:
            delays = self.db.delays_for(row.leg_key)
            if delays or row.delay_minutes >= self.departed_threshold:
                if not seeding:
                    self.db.enqueue(notify.departure_message(
                        flight, delays, self.config.include_flight_number,
                        self.config.show_total_delay))
                    result["departures"] += 1
            self.db.mark_departure_notified(row.leg_key)

        # 3. Рейс отменён.
        if row.is_cancelled and not was_cancelled:
            result["cancelled"] += 1
            if not seeding and self.config.notify_cancelled:
                self.db.enqueue(notify.cancelled_message(
                    flight, self.config.include_flight_number))

    def _record_delay(self, row, previous_expected, detected, seeding, result):
        step = row.delay_minutes
        if previous_expected is not None:
            step = int((row.expected - previous_expected).total_seconds() // 60)

        is_new = self.db.add_delay(
            leg_key=row.leg_key,
            previous_expected=previous_expected,
            new_expected=row.expected,
            detected=detected,
            delay_minutes=row.delay_minutes,
            step_minutes=step,
        )
        if not is_new:
            return  # это время уже объявляли — повторно не оповещаем

        result["delays"] += 1
        if seeding:
            return

        flight = self.db.get_flight(row.leg_key)
        delays = self.db.delays_for(row.leg_key)
        self.db.enqueue(notify.delay_message(
            flight, delays, self.config.include_flight_number,
            self.config.show_total_delay))

    # -- ежедневная чистка ------------------------------------------------

    def cleanup_if_due(self):
        """Раз в сутки удалять записи старше keep_days."""
        last = parse_iso(self.db.get_setting(LAST_CLEANUP_KEY))
        now = now_utc()
        if last is not None and (now - last).total_seconds() < 24 * 3600:
            return None
        removed = self.db.cleanup(self.config.keep_days)
        self.db.set_setting(LAST_CLEANUP_KEY, iso(now))
        log.info("чистка: удалено %s", removed)
        return removed

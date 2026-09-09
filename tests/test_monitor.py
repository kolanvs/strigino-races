"""Сценарий из ТЗ: серия задержек и вылет, с проверкой текстов сообщений."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strigino import config as config_module, monitor as monitor_module
from strigino.board import BoardRow
from strigino.db import Database
from strigino.monitor import Monitor
from strigino.timeutil import MSK

SEPT = lambda day, hour, minute: datetime(2026, 9, day, hour, minute, tzinfo=MSK)


def make_row(scheduled, expected, status="", flight_no="ZF-443", uid="6060990",
             dest_name="Анталья", dest_iata="AYT"):
    return BoardRow(
        kind="dep", site_uid=uid, flight_no=flight_no, airline="Azur Air",
        dest_name=dest_name, dest_iata=dest_iata,
        scheduled=scheduled, expected=expected, status=status, note="",
        href="/board/dep-zf-%s/" % uid,
    )


class MonitorTestCase(unittest.TestCase):
    """База в файле + подменённые время и загрузка табло."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.path)
        self.db = Database(self.path)
        self.config = config_module.load(None)
        self.config.min_delay_minutes = 1
        self.monitor = Monitor(self.db, self.config)

        self._real_fetch = monitor_module.fetch_departures
        self._real_now = monitor_module.now_utc

    def tearDown(self):
        monitor_module.fetch_departures = self._real_fetch
        monitor_module.now_utc = self._real_now
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass

    def poll_at(self, moment, rows):
        """Прогнать один цикл, зафиксировав "текущее" время."""
        monitor_module.fetch_departures = lambda **kwargs: list(rows)
        monitor_module.now_utc = lambda: moment
        return self.monitor.poll()

    def messages(self):
        return [row["text"] for row in
                self.db.conn.execute("SELECT text FROM messages ORDER BY id")]


class TestDelayScenario(MonitorTestCase):
    """Рейс Нижний Новгород-Анталья, вылет 9 сентября 20:50."""

    SCHEDULED = SEPT(9, 20, 50)

    def setUp(self):
        super().setUp()
        self.db.add_chat("100500", "тест")

    def test_first_poll_is_silent(self):
        """Первый запуск наполняет базу, но не рассылает историю задержек."""
        self.poll_at(SEPT(9, 19, 0),
                     [make_row(self.SCHEDULED, SEPT(9, 22, 5))])
        self.assertEqual(self.messages(), [])
        self.assertEqual(len(self.db.delays_for("dep-6060990-AYT")), 1)

    def test_full_scenario(self):
        # 19:00 — рейс по расписанию, база наполняется молча.
        self.poll_at(SEPT(9, 19, 0), [make_row(self.SCHEDULED, self.SCHEDULED)])
        self.assertEqual(self.messages(), [])

        # 21:02 — первая задержка: 20:50 -> 22:05.
        self.poll_at(SEPT(9, 21, 2), [make_row(self.SCHEDULED, SEPT(9, 22, 5))])
        messages = self.messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(
            messages[0],
            "⚠️ <b>Задержан рейс ZF-443</b>\n"
            "Нижний Новгород → Анталья · <code>GOJ-AYT</code>\n"
            "\n"
            "По расписанию: <b>9 сентября 20:50</b>\n"
            "Новое время: <b>22:05</b>\n"
            "Задержка: <b>1 ч 15 мин</b>\n"
            "\n"
            "<i>Обнаружено 21:02</i>")

        # 21:15 — ничего не изменилось, повторов быть не должно.
        self.poll_at(SEPT(9, 21, 15), [make_row(self.SCHEDULED, SEPT(9, 22, 5))])
        self.assertEqual(len(self.messages()), 1)

        # 21:27 — вторая задержка.
        self.poll_at(SEPT(9, 21, 27), [make_row(self.SCHEDULED, SEPT(9, 22, 40))])
        # 21:57 — третья: сообщение должно перечислить обе предыдущие.
        self.poll_at(SEPT(9, 21, 57), [make_row(self.SCHEDULED, SEPT(9, 23, 0))])

        messages = self.messages()
        self.assertEqual(len(messages), 3)
        self.assertEqual(
            messages[2],
            "⚠️ <b>Задержан рейс ZF-443</b> · задержка №3\n"
            "Нижний Новгород → Анталья · <code>GOJ-AYT</code>\n"
            "\n"
            "По расписанию: <b>9 сентября 20:50</b>\n"
            "Новое время: <b>23:00</b>\n"
            "Задержка: <b>2 ч 10 мин</b>\n"
            "\n"
            "Объявляли ранее:\n"
            "• 22:05 — обнаружено 21:02\n"
            "• 22:40 — обнаружено 21:27\n"
            "\n"
            "<i>Обнаружено 21:57</i>")

        # 22:33 — четвёртая задержка.
        self.poll_at(SEPT(9, 22, 33), [make_row(self.SCHEDULED, SEPT(9, 23, 30))])

        # 00:15 десятого — рейс улетел в 00:10.
        self.poll_at(SEPT(10, 0, 15),
                     [make_row(self.SCHEDULED, SEPT(10, 0, 10), status="Вылетел")])

        messages = self.messages()
        self.assertEqual(len(messages), 5)
        self.assertEqual(
            messages[4],
            "\U0001f6eb <b>Вылетел рейс ZF-443</b>\n"
            "Нижний Новгород → Анталья · <code>GOJ-AYT</code>\n"
            "\n"
            "Фактический вылет: <b>10 сентября 00:10</b>\n"
            "Итоговая задержка: <b>3 ч 20 мин</b>\n"
            "\n"
            "История:\n"
            "• по расписанию — 20:50\n"
            "• 22:05 — обнаружено 21:02\n"
            "• 22:40 — обнаружено 21:27\n"
            "• 23:00 — обнаружено 21:57\n"
            "• 23:30 — обнаружено 22:33")

        # Повторный опрос после вылета ничего не добавляет.
        self.poll_at(SEPT(10, 0, 30),
                     [make_row(self.SCHEDULED, SEPT(10, 0, 10), status="Вылетел")])
        self.assertEqual(len(self.messages()), 5)


class TestNotificationRules(MonitorTestCase):
    def setUp(self):
        super().setUp()
        self.db.add_chat("100500", "тест")
        self.poll_at(SEPT(9, 10, 0), [])  # снять флаг первого запуска

    def test_on_time_flight_is_quiet(self):
        row = make_row(SEPT(9, 20, 50), SEPT(9, 20, 50))
        self.poll_at(SEPT(9, 19, 0), [row])
        self.poll_at(SEPT(9, 20, 0), [row])
        self.assertEqual(self.messages(), [])

    def test_early_departure_is_not_a_delay(self):
        self.poll_at(SEPT(9, 19, 0), [make_row(SEPT(9, 20, 50), SEPT(9, 20, 50))])
        self.poll_at(SEPT(9, 19, 30), [make_row(SEPT(9, 20, 50), SEPT(9, 20, 40))])
        self.assertEqual(self.messages(), [])

    def test_small_actual_departure_offset_is_quiet(self):
        """Вылет на 5 минут позже плана без объявленной задержки — не событие.

        Колонка ожидаемого времени у вылетевшего рейса означает
        фактическое время отправления, и такие мелкие расхождения есть
        почти у каждого рейса.
        """
        self.poll_at(SEPT(9, 9, 0), [make_row(SEPT(9, 9, 10), SEPT(9, 9, 10))])
        self.poll_at(SEPT(9, 9, 20),
                     [make_row(SEPT(9, 9, 10), SEPT(9, 9, 15), status="Вылетел")])
        self.assertEqual(self.messages(), [])

    def test_large_actual_departure_offset_reports_departure(self):
        """А вот вылет на полчаса позже плана сообщить нужно."""
        self.poll_at(SEPT(9, 9, 0), [make_row(SEPT(9, 9, 10), SEPT(9, 9, 10))])
        self.poll_at(SEPT(9, 9, 45),
                     [make_row(SEPT(9, 9, 10), SEPT(9, 9, 40), status="Вылетел")])
        messages = self.messages()
        self.assertEqual(len(messages), 1)
        self.assertIn("Вылетел рейс", messages[0])
        self.assertIn("Итоговая задержка: <b>30 мин</b>", messages[0])

    def test_threshold_filters_small_delays(self):
        self.db.set_setting("min_delay_minutes", 15)
        self.poll_at(SEPT(9, 19, 0), [make_row(SEPT(9, 20, 50), SEPT(9, 20, 50))])
        self.poll_at(SEPT(9, 19, 10), [make_row(SEPT(9, 20, 50), SEPT(9, 21, 0))])
        self.assertEqual(self.messages(), [])
        self.poll_at(SEPT(9, 19, 20), [make_row(SEPT(9, 20, 50), SEPT(9, 21, 30))])
        self.assertEqual(len(self.messages()), 1)

    def test_flight_appearing_already_delayed(self):
        """Задержку объявляют и до того, как рейс впервые попал в базу."""
        self.poll_at(SEPT(8, 12, 0), [make_row(SEPT(9, 20, 50), SEPT(9, 23, 0))])
        messages = self.messages()
        self.assertEqual(len(messages), 1)
        self.assertIn("Новое время: <b>23:00</b>", messages[0])
        self.assertNotIn("Объявляли ранее", messages[0])
        self.assertNotIn("задержка №", messages[0])
        # Обнаружено накануне — значит у времени обнаружения нужна дата.
        self.assertIn("<i>Обнаружено 8 сентября 12:00</i>", messages[0])

    def test_cancelled_flight(self):
        self.poll_at(SEPT(9, 19, 0), [make_row(SEPT(9, 20, 50), SEPT(9, 20, 50))])
        self.poll_at(SEPT(9, 19, 30),
                     [make_row(SEPT(9, 20, 50), SEPT(9, 20, 50), status="Отменен")])
        messages = self.messages()
        self.assertEqual(len(messages), 1)
        self.assertIn("<b>Отменён рейс ZF-443</b>", messages[0])

    def test_codeshare_notified_once(self):
        """Два номера одного самолёта дают одно сообщение."""
        merged = make_row(SEPT(9, 18, 0), SEPT(9, 22, 2),
                          flight_no="EO-829 / N4-829", uid="6061030",
                          dest_name="Уфа", dest_iata="UFA")
        self.poll_at(SEPT(9, 17, 0),
                     [make_row(SEPT(9, 18, 0), SEPT(9, 18, 0),
                               flight_no="EO-829 / N4-829", uid="6061030",
                               dest_name="Уфа", dest_iata="UFA")])
        self.poll_at(SEPT(9, 17, 30), [merged])
        messages = self.messages()
        self.assertEqual(len(messages), 1)
        self.assertIn("EO-829 / N4-829", messages[0])

    def test_message_queued_for_every_active_chat(self):
        self.db.add_chat("200600", "второй")
        self.poll_at(SEPT(9, 19, 0), [make_row(SEPT(9, 20, 50), SEPT(9, 20, 50))])
        self.poll_at(SEPT(9, 19, 30), [make_row(SEPT(9, 20, 50), SEPT(9, 21, 30))])
        chats = [row["chat_id"] for row in
                 self.db.conn.execute("SELECT chat_id FROM messages")]
        self.assertEqual(sorted(chats), ["100500", "200600"])

    def test_board_failure_does_not_lose_state(self):
        self.poll_at(SEPT(9, 19, 0), [make_row(SEPT(9, 20, 50), SEPT(9, 20, 50))])

        def boom(**kwargs):
            raise monitor_module.BoardError("сеть недоступна")

        monitor_module.fetch_departures = boom
        result = self.monitor.poll()
        self.assertIn("error", result)
        self.assertIsNotNone(self.db.get_flight("dep-6060990-AYT"))


class TestCleanup(MonitorTestCase):
    def test_removes_records_older_than_keep_days(self):
        self.poll_at(SEPT(9, 10, 0), [])
        old = SEPT(9, 20, 50) - timedelta(days=20)
        self.poll_at(old - timedelta(hours=2), [make_row(old, old)])
        self.poll_at(old - timedelta(hours=1),
                     [make_row(old, old + timedelta(minutes=40))])

        self.assertEqual(self.db.stats()["flights"], 1)
        self.assertEqual(self.db.stats()["delays"], 1)

        self.db.cleanup(keep_days=14)
        self.assertEqual(self.db.stats()["flights"], 0)
        self.assertEqual(self.db.stats()["delays"], 0)  # ушли каскадом

    def test_recent_records_survive(self):
        self.poll_at(SEPT(9, 10, 0), [])
        monitor_module.now_utc = self._real_now
        recent = monitor_module.now_utc().astimezone(MSK)
        self.poll_at(recent, [make_row(recent, recent)])
        self.db.cleanup(keep_days=14)
        self.assertEqual(self.db.stats()["flights"], 1)


if __name__ == "__main__":
    unittest.main()

"""Разбор реальных страниц табло, сохранённых в tests/fixtures."""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strigino.board import merge_codeshares, parse_board
from strigino.timeutil import MSK

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
# Страницы сняты вечером 8 сентября 2026 года.
REFERENCE = datetime(2026, 9, 8, 21, 40, tzinfo=MSK)


def load(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return parse_board(handle.read(), REFERENCE)


def departures(name):
    return merge_codeshares([row for row in load(name) if row.kind == "dep"])


class TestParsing(unittest.TestCase):
    def test_today_row_count(self):
        self.assertEqual(len(load("board_today.html")), 17)

    def test_fields_of_known_flight(self):
        row = next(r for r in departures("board_today.html")
                   if r.site_uid == "6060990")
        self.assertEqual(row.flight_no, "ZF-443")
        self.assertEqual(row.dest_name, "Анталья")
        self.assertEqual(row.dest_iata, "AYT")
        self.assertEqual(row.route, "Нижний Новгород-Анталья (GOJ-AYT)")
        self.assertEqual(row.status, "Вылетел")
        self.assertTrue(row.is_departed)
        self.assertFalse(row.is_cancelled)
        self.assertEqual(row.scheduled, datetime(2026, 9, 8, 2, 5, tzinfo=MSK))
        self.assertEqual(row.expected, datetime(2026, 9, 8, 2, 32, tzinfo=MSK))
        self.assertEqual(row.delay_minutes, 27)

    def test_delay_across_midnight(self):
        """Вылет 7 сентября в 22:15 фактически состоялся 8-го в 02:24."""
        row = next(r for r in departures("board_today.html")
                   if r.site_uid == "6061080")
        self.assertEqual(row.scheduled, datetime(2026, 9, 7, 22, 15, tzinfo=MSK))
        self.assertEqual(row.expected, datetime(2026, 9, 8, 2, 24, tzinfo=MSK))
        self.assertEqual(row.delay_minutes, 249)

    def test_early_departure_is_negative(self):
        row = next(r for r in departures("board_today.html")
                   if r.site_uid == "6061380")
        self.assertEqual(row.delay_minutes, -12)

    def test_future_flight_has_no_delay(self):
        """У завтрашнего рейса без задержки ожидаемое время равно плановому."""
        row = next(r for r in departures("board_tomorrow.html")
                   if r.site_uid == "6061470")
        self.assertEqual(row.status, "")
        self.assertEqual(row.delay_minutes, 0)
        self.assertFalse(row.is_departed)

    def test_delay_announced_in_advance(self):
        """Задержку объявляют заранее — до дня вылета."""
        row = next(r for r in departures("board_tomorrow.html")
                   if r.site_uid == "6061990")
        self.assertEqual(row.delay_minutes, 35)
        self.assertFalse(row.is_departed)

    def test_codeshare_merged_into_one_flight(self):
        """EO-829 и N4-829 — один самолёт, две строки на табло."""
        rows = [r for r in load("board_yesterday.html") if r.site_uid == "6061030"]
        self.assertEqual(len(rows), 2)

        merged = [r for r in departures("board_yesterday.html")
                  if r.site_uid == "6061030"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].flight_no, "EO-829 / N4-829")
        self.assertEqual(merged[0].delay_minutes, 242)

    def test_leg_key_is_unique_per_flight(self):
        for name in ("board_today.html", "board_yesterday.html", "board_tomorrow.html"):
            rows = departures(name)
            keys = [row.leg_key for row in rows]
            self.assertEqual(len(keys), len(set(keys)), name)

    def test_multi_leg_arrival_splits_by_destination(self):
        """Рейс с посадкой даёт два плеча с одним site_uid, но разными IATA."""
        rows = [r for r in load("board_arrivals.html") if r.site_uid == "6060840"]
        if not rows:
            self.skipTest("фикстура прилётов отсутствует")
        self.assertEqual(len({r.leg_key for r in rows}), len(rows))

    def test_no_html_leaks_into_fields(self):
        for row in departures("board_today.html"):
            for value in (row.flight_no, row.airline, row.dest_name, row.status, row.note):
                self.assertNotIn("<", value)
                self.assertNotIn("&nbsp", value)


if __name__ == "__main__":
    unittest.main()

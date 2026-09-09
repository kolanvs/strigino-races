"""Проверка HTML-разметки сообщений.

Сообщения уходят с parse_mode=HTML, и битая разметка — это отказ Telegram
с кодом 400. Данные в сообщения попадают прямо с сайта, поэтому проверяется
и то, что они экранируются.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strigino import notify
from strigino.timeutil import MSK, iso

# Теги, которые Telegram понимает в parse_mode=HTML.
ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
                "a", "code", "pre", "span", "tg-spoiler", "blockquote"}


class HtmlChecker(HTMLParser):
    """Собирает нарушения: чужие теги и незакрытые пары."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack = []
        self.problems = []

    def handle_starttag(self, tag, attrs):
        if tag not in ALLOWED_TAGS:
            self.problems.append("недопустимый тег <%s>" % tag)
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack:
            self.problems.append("закрывающий </%s> без открывающего" % tag)
        elif self.stack[-1] != tag:
            self.problems.append("</%s> закрывает <%s>" % (tag, self.stack[-1]))
        else:
            self.stack.pop()

    def finish(self):
        for tag in self.stack:
            self.problems.append("незакрытый <%s>" % tag)
        return self.problems


def check_html(text):
    checker = HtmlChecker()
    checker.feed(text)
    checker.close()
    return checker.finish()


def flight(dest_name="Анталья", dest_iata="AYT", flight_no="ZF-443",
           kind="dep", scheduled=None, expected=None):
    scheduled = scheduled or datetime(2026, 9, 9, 20, 50, tzinfo=MSK)
    expected = expected or datetime(2026, 9, 10, 0, 10, tzinfo=MSK)
    return {
        "flight_no": flight_no, "dest_name": dest_name, "dest_iata": dest_iata,
        "kind": kind, "route": "Нижний Новгород-%s (GOJ-%s)" % (dest_name, dest_iata),
        "scheduled_utc": iso(scheduled), "expected_utc": iso(expected),
    }


def delay(new_expected, detected, minutes):
    return {"new_expected_utc": iso(new_expected), "detected_utc": iso(detected),
            "delay_minutes": minutes, "step_minutes": minutes}


D = lambda day, hour, minute: datetime(2026, 9, day, hour, minute, tzinfo=MSK)


class TestHtmlValidity(unittest.TestCase):
    def assertValidHtml(self, text):
        problems = check_html(text)
        self.assertEqual(problems, [], "%s\n---\n%s" % (problems, text))

    def test_all_message_kinds_are_valid_html(self):
        one = [delay(D(9, 22, 5), D(9, 21, 2), 75)]
        many = one + [delay(D(9, 23, 0), D(9, 21, 57), 130)]

        self.assertValidHtml(notify.delay_message(flight(), one))
        self.assertValidHtml(notify.delay_message(flight(), many))
        self.assertValidHtml(notify.departure_message(flight(), many))
        self.assertValidHtml(notify.cancelled_message(flight()))
        self.assertValidHtml(notify.flights_message([flight()]))
        self.assertValidHtml(notify.flights_message([]))
        self.assertValidHtml(notify.HELP_TEXT)
        self.assertValidHtml(notify.SUBSCRIBED_TEXT)

    def test_status_message_including_error(self):
        stats = {"flights": 1, "delays": 2, "pending": 0, "sent": 3, "chats": 1}
        self.assertValidHtml(notify.status_message(5, 1, stats, D(9, 8, 42)))
        self.assertValidHtml(notify.status_message(
            5, 1, stats, None, last_error="сбой <сети> & прочее"))

    def test_dangerous_data_is_escaped(self):
        """Данные с сайта не должны прорываться в разметку."""
        evil = flight(dest_name="<b>Анталья</b> & Ко", flight_no="<script>")
        one = [delay(D(9, 22, 5), D(9, 21, 2), 75)]

        for text in (notify.delay_message(evil, one),
                     notify.departure_message(evil, one),
                     notify.cancelled_message(evil),
                     notify.flights_message([evil])):
            self.assertValidHtml(text)
            self.assertNotIn("<script>", text)
            self.assertIn("&lt;", text)
            self.assertIn("&amp;", text)


class TestFormatting(unittest.TestCase):
    def test_date_shown_only_when_it_differs(self):
        """Внутри одних суток дата не повторяется, при переносе — печатается."""
        same_day = notify.delay_message(
            flight(), [delay(D(9, 22, 5), D(9, 21, 2), 75)])
        self.assertIn("Новое время: <b>22:05</b>", same_day)

        next_day = notify.delay_message(
            flight(), [delay(D(10, 0, 40), D(9, 21, 2), 230)])
        self.assertIn("Новое время: <b>10 сентября 00:40</b>", next_day)

    def test_repeat_delay_is_numbered(self):
        one = [delay(D(9, 22, 5), D(9, 21, 2), 75)]
        self.assertNotIn("задержка №", notify.delay_message(flight(), one))

        three = one + [delay(D(9, 22, 40), D(9, 21, 27), 110),
                       delay(D(9, 23, 0), D(9, 21, 57), 130)]
        self.assertIn("задержка №3", notify.delay_message(flight(), three))

    def test_flights_list_groups_by_day_and_omits_origin(self):
        rows = [
            flight(dest_name="Сочи", dest_iata="AER", flight_no="N4-440",
                   scheduled=D(9, 17, 15), expected=D(9, 17, 15)),
            flight(dest_name="Норильск", dest_iata="NSK", flight_no="Y7-926",
                   scheduled=D(9, 22, 15), expected=D(9, 22, 15)),
            flight(dest_name="Анталья", dest_iata="AYT", flight_no="U6-1579",
                   scheduled=D(10, 1, 0), expected=D(10, 1, 0)),
        ]
        text = notify.flights_message(rows)

        self.assertEqual(text.count("<b>9 сентября</b>"), 1)
        self.assertEqual(text.count("<b>10 сентября</b>"), 1)
        self.assertIn("17:15 · N4-440 → Сочи", text)
        # Пункт отправления у всех строк один и тот же — в списке его нет.
        self.assertNotIn("Нижний Новгород", text)

    def test_flights_list_marks_delay(self):
        rows = [flight(dest_name="Сочи", dest_iata="AER", flight_no="FV-6644",
                       scheduled=D(9, 14, 55), expected=D(9, 15, 30))]
        text = notify.flights_message(rows)
        self.assertIn("⚠️", text)
        self.assertIn("<b>15:30</b>", text)
        self.assertIn("(+35 мин)", text)

    def test_city_with_hyphen_survives(self):
        """«Санкт-Петербург» не должен разрезаться по дефису."""
        text = notify.delay_message(
            flight(dest_name="Санкт-Петербург", dest_iata="LED"),
            [delay(D(9, 22, 5), D(9, 21, 2), 75)])
        self.assertIn("Нижний Новгород → Санкт-Петербург", text)


if __name__ == "__main__":
    unittest.main()

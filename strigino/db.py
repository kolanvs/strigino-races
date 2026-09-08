"""Хранилище: SQLite.

Три содержательные таблицы:

* ``flights``  — последнее известное состояние каждого рейса;
* ``delays``   — история объявленных времён вылета (по строке на задержку);
* ``messages`` — очередь исходящих сообщений.

Очередь нужна, чтобы оповещение не пропало, если Telegram недоступен или
процесс перезапустили: факт задержки и факт её отправки фиксируются
отдельно. Раз в сутки записи старше 14 дней удаляются.
"""

import sqlite3
import threading
from datetime import timedelta

from .timeutil import iso, now_utc

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS flights (
    leg_key            TEXT PRIMARY KEY,
    kind               TEXT NOT NULL,
    site_uid           TEXT NOT NULL,
    flight_no          TEXT,
    airline            TEXT,
    dest_name          TEXT,
    dest_iata          TEXT,
    route              TEXT,
    scheduled_utc      TEXT NOT NULL,
    expected_utc       TEXT NOT NULL,
    status             TEXT,
    note               TEXT,
    href               TEXT,
    departed           INTEGER NOT NULL DEFAULT 0,
    cancelled          INTEGER NOT NULL DEFAULT 0,
    departed_notified  INTEGER NOT NULL DEFAULT 0,
    first_seen_utc     TEXT NOT NULL,
    last_seen_utc      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_flights_scheduled ON flights(scheduled_utc);

CREATE TABLE IF NOT EXISTS delays (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    leg_key               TEXT NOT NULL REFERENCES flights(leg_key) ON DELETE CASCADE,
    previous_expected_utc TEXT,
    new_expected_utc      TEXT NOT NULL,
    detected_utc          TEXT NOT NULL,
    delay_minutes         INTEGER NOT NULL,
    step_minutes          INTEGER NOT NULL,
    UNIQUE(leg_key, new_expected_utc)
);

CREATE INDEX IF NOT EXISTS idx_delays_leg ON delays(leg_key, id);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      TEXT NOT NULL,
    text         TEXT NOT NULL,
    created_utc  TEXT NOT NULL,
    sent_utc     TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_pending ON messages(sent_utc, id);

CREATE TABLE IF NOT EXISTS chats (
    chat_id    TEXT PRIMARY KEY,
    title      TEXT,
    added_utc  TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


class Database:
    """Тонкая обёртка над sqlite3. Все времена хранятся в UTC ISO-8601."""

    def __init__(self, path):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, timeout=30,
                                    check_same_thread=False,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Опрос табло и long polling бота пишут из разных потоков; блокировка
        # делает атомарными не только отдельные запросы, но и связки
        # "записать задержку -> прочитать её историю".
        self.lock = threading.RLock()

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    # -- настройки ------------------------------------------------------

    def get_setting(self, key, default=None):
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key, value):
        with self.lock:
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)))

    def get_int_setting(self, key, default):
        try:
            return int(self.get_setting(key, default))
        except (TypeError, ValueError):
            return default

    # -- чаты -----------------------------------------------------------

    def add_chat(self, chat_id, title=""):
        """Зарегистрировать чат-получатель. Возвращает True, если он новый."""
        with self.lock:
            cursor = self.conn.execute(
                "INSERT INTO chats(chat_id, title, added_utc, active) VALUES(?, ?, ?, 1) "
                "ON CONFLICT(chat_id) DO UPDATE SET active = 1, title = excluded.title",
                (str(chat_id), title, iso(now_utc())))
            return cursor.rowcount == 1

    def deactivate_chat(self, chat_id):
        with self.lock:
            self.conn.execute("UPDATE chats SET active = 0 WHERE chat_id = ?", (str(chat_id),))

    def active_chats(self):
        return [row["chat_id"] for row in
                self.conn.execute("SELECT chat_id FROM chats WHERE active = 1")]

    # -- рейсы ----------------------------------------------------------

    def get_flight(self, leg_key):
        return self.conn.execute(
            "SELECT * FROM flights WHERE leg_key = ?", (leg_key,)).fetchone()

    def upsert_flight(self, row, departed=None, cancelled=None):
        """Записать текущее состояние рейса, сохранив служебные флаги."""
        with self.lock:
            now = iso(now_utc())
            departed = int(row.is_departed if departed is None else departed)
            cancelled = int(row.is_cancelled if cancelled is None else cancelled)
            self.conn.execute(
                "INSERT INTO flights(leg_key, kind, site_uid, flight_no, airline,"
                " dest_name, dest_iata, route, scheduled_utc, expected_utc, status,"
                " note, href, departed, cancelled, departed_notified,"
                " first_seen_utc, last_seen_utc)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)"
                " ON CONFLICT(leg_key) DO UPDATE SET"
                "   flight_no = excluded.flight_no,"
                "   airline = excluded.airline,"
                "   dest_name = excluded.dest_name,"
                "   route = excluded.route,"
                "   scheduled_utc = excluded.scheduled_utc,"
                "   expected_utc = excluded.expected_utc,"
                "   status = excluded.status,"
                "   note = excluded.note,"
                "   href = excluded.href,"
                "   departed = excluded.departed,"
                "   cancelled = excluded.cancelled,"
                "   last_seen_utc = excluded.last_seen_utc",
                (row.leg_key, row.kind, row.site_uid, row.flight_no, row.airline,
                 row.dest_name, row.dest_iata, row.route, iso(row.scheduled),
                 iso(row.expected), row.status, row.note, row.href,
                 departed, cancelled, now, now))

    def mark_departure_notified(self, leg_key):
        with self.lock:
            self.conn.execute(
                "UPDATE flights SET departed_notified = 1 WHERE leg_key = ?", (leg_key,))

    def active_flights(self):
        """Рейсы, которые ещё не улетели и не отменены."""
        return self.conn.execute(
            "SELECT * FROM flights WHERE departed = 0 AND cancelled = 0"
            " ORDER BY expected_utc").fetchall()

    # -- задержки -------------------------------------------------------

    def add_delay(self, leg_key, previous_expected, new_expected, detected,
                  delay_minutes, step_minutes):
        """Записать объявленное время вылета.

        UNIQUE(leg_key, new_expected_utc) защищает от повторной записи,
        если тот же опрос обработается дважды после перезапуска.
        """
        with self.lock:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO delays(leg_key, previous_expected_utc,"
                " new_expected_utc, detected_utc, delay_minutes, step_minutes)"
                " VALUES(?,?,?,?,?,?)",
                (leg_key,
                 iso(previous_expected) if previous_expected else None,
                 iso(new_expected), iso(detected), int(delay_minutes), int(step_minutes)))
            return cursor.rowcount == 1

    def delays_for(self, leg_key):
        return self.conn.execute(
            "SELECT * FROM delays WHERE leg_key = ? ORDER BY id", (leg_key,)).fetchall()

    # -- очередь сообщений ----------------------------------------------

    def enqueue(self, text, chat_ids=None):
        """Поставить сообщение в очередь всем активным чатам."""
        with self.lock:
            targets = chat_ids if chat_ids is not None else self.active_chats()
            now = iso(now_utc())
            for chat_id in targets:
                self.conn.execute(
                    "INSERT INTO messages(chat_id, text, created_utc) VALUES(?,?,?)",
                    (str(chat_id), text, now))
            return len(targets)

    def pending_messages(self, limit=20, max_attempts=10):
        return self.conn.execute(
            "SELECT * FROM messages WHERE sent_utc IS NULL AND attempts < ?"
            " ORDER BY id LIMIT ?", (max_attempts, limit)).fetchall()

    def mark_sent(self, message_id):
        with self.lock:
            self.conn.execute("UPDATE messages SET sent_utc = ?, last_error = NULL"
                              " WHERE id = ?", (iso(now_utc()), message_id))

    def mark_failed(self, message_id, error):
        with self.lock:
            self.conn.execute(
                "UPDATE messages SET attempts = attempts + 1, last_error = ?"
                " WHERE id = ?", (str(error)[:500], message_id))

        # -- обслуживание ---------------------------------------------------

    def cleanup(self, keep_days=14):
        """Удалить рейсы и отправленные сообщения старше keep_days.

        Задержки уходят каскадом вместе с рейсом.
        """
        cutoff = iso(now_utc() - timedelta(days=keep_days))
        with self.lock:
            flights = self.conn.execute(
                "DELETE FROM flights WHERE scheduled_utc < ?", (cutoff,)).rowcount
            messages = self.conn.execute(
                "DELETE FROM messages WHERE sent_utc IS NOT NULL AND sent_utc < ?",
                (cutoff,)).rowcount
            stale = self.conn.execute(
                "DELETE FROM messages WHERE sent_utc IS NULL AND created_utc < ?",
                (cutoff,)).rowcount
            # VACUUM только когда есть что уплотнять: на роутере это лишняя
            # перезапись флеш-памяти.
            if flights or messages or stale:
                self.conn.execute("VACUUM")
        return {"flights": flights, "messages": messages + stale}

    def stats(self):
        one = lambda sql: self.conn.execute(sql).fetchone()[0]
        return {
            "flights": one("SELECT COUNT(*) FROM flights"),
            "delays": one("SELECT COUNT(*) FROM delays"),
            "pending": one("SELECT COUNT(*) FROM messages WHERE sent_utc IS NULL"),
            "sent": one("SELECT COUNT(*) FROM messages WHERE sent_utc IS NOT NULL"),
            "chats": one("SELECT COUNT(*) FROM chats WHERE active = 1"),
        }

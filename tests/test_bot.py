"""Управление ботом: главное — задать период опроса числом от 2 до 30."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strigino import config as config_module
from strigino.bot import BotHandler
from strigino.db import Database
from strigino.monitor import Monitor


def message(text, chat_id=100500, update_id=1):
    return {"update_id": update_id,
            "message": {"chat": {"id": chat_id, "type": "private",
                                 "first_name": "Николай"},
                        "text": text}}


class TestBotHandler(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        os.unlink(self.path)
        self.db = Database(self.path)
        self.config = config_module.load(None)
        self.monitor = Monitor(self.db, self.config)
        self.handler = BotHandler(self.db, self.monitor, self.config)

    def tearDown(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass

    def reply(self, text, chat_id=100500):
        return self.handler.handle_update(message(text, chat_id))[1]

    # -- периодичность ---------------------------------------------------

    def test_number_sets_poll_interval(self):
        self.assertEqual(self.monitor.interval_minutes, 5)
        reply = self.reply("15")
        self.assertIn("15 мин", reply)
        self.assertEqual(self.monitor.interval_minutes, 15)

    def test_interval_bounds(self):
        for value in ("2", "30"):
            self.reply(value)
            self.assertEqual(self.monitor.interval_minutes, int(value))

        for value in ("1", "31", "0", "100"):
            reply = self.reply(value)
            self.assertIn("от 2 до 30", reply)
        self.assertEqual(self.monitor.interval_minutes, 30)  # не изменился

    def test_interval_survives_whitespace(self):
        self.reply("  7  ")
        self.assertEqual(self.monitor.interval_minutes, 7)

    def test_interval_persists_in_database(self):
        self.reply("12")
        other = Database(self.path)
        self.assertEqual(other.get_int_setting("poll_interval_minutes", 5), 12)
        other.close()

    def test_sending_number_also_subscribes(self):
        self.reply("10")
        self.assertEqual(self.db.active_chats(), ["100500"])

    # -- прочие команды ---------------------------------------------------

    def test_start_subscribes_and_stop_unsubscribes(self):
        self.reply("/start")
        self.assertEqual(self.db.active_chats(), ["100500"])
        self.reply("/stop")
        self.assertEqual(self.db.active_chats(), [])

    def test_command_with_bot_suffix(self):
        reply = self.reply("/status@strigino_bot")
        self.assertIn("Период опроса", reply)

    def test_threshold(self):
        self.assertIn("10 мин", self.reply("/threshold 10"))
        self.assertEqual(self.monitor.threshold, 10)
        self.assertIn("от 1 до 600", self.reply("/threshold 900"))
        self.assertIn("Укажите порог", self.reply("/threshold"))

    def test_unknown_text_gets_help(self):
        self.assertIn("число от 2 до 30", self.reply("привет"))

    def test_flights_on_empty_database(self):
        self.assertIn("нет предстоящих", self.reply("/flights"))

    def test_ignores_updates_without_text(self):
        chat_id, reply = self.handler.handle_update(
            {"update_id": 2, "message": {"chat": {"id": 1}}})
        self.assertIsNone(chat_id)
        self.assertIsNone(reply)

    # -- ограничение доступа ----------------------------------------------

    def test_allowed_chat_ids_block_strangers(self):
        self.config.allowed_chat_ids = ["100500"]
        self.assertIn("Период опроса", self.reply("/status", chat_id=100500))
        self.assertIn("не в списке", self.reply("/status", chat_id=999))

    def test_empty_allow_list_permits_everyone(self):
        self.assertIn("Период опроса", self.reply("/status", chat_id=999))


if __name__ == "__main__":
    unittest.main()

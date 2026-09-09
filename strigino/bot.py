"""Обработка входящих сообщений бота.

Главная команда по ТЗ — просто число от 2 до 30: период опроса табло в
минутах. Остальное — вспомогательные команды.
"""

import logging
import re

from . import notify
from .telegram import TelegramError
from .timeutil import parse_iso

log = logging.getLogger("strigino.bot")

MIN_INTERVAL = 2
MAX_INTERVAL = 30

_NUMBER_RE = re.compile(r"^\s*(\d{1,3})\s*$")


class BotHandler:
    """Разбирает команды и обновляет настройки в базе."""

    def __init__(self, db, monitor, config):
        self.db = db
        self.monitor = monitor
        self.config = config

    # -- доступ ----------------------------------------------------------

    def _is_allowed(self, chat_id):
        """Пускать либо перечисленные в конфиге чаты, либо любые.

        Если allowed_chat_ids пуст, бот открыт: это удобно для личного
        бота, чей токен известен только владельцу.
        """
        if not self.config.allowed_chat_ids:
            return True
        return str(chat_id) in {str(x) for x in self.config.allowed_chat_ids}

    # -- обработка -------------------------------------------------------

    def handle_update(self, update):
        """Вернуть текст ответа (или None, если отвечать не нужно)."""
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        text = (message.get("text") or "").strip()
        if chat_id is None or not text:
            return None, None

        if not self._is_allowed(chat_id):
            log.warning("сообщение из неразрешённого чата %s", chat_id)
            return chat_id, "Этот чат не в списке разрешённых."

        title = chat.get("title") or " ".join(
            filter(None, [chat.get("first_name"), chat.get("last_name")])) or ""

        command = text.split()[0].lower().split("@")[0]

        if command in ("/start", "/subscribe"):
            self.db.add_chat(chat_id, title)
            return chat_id, notify.SUBSCRIBED_TEXT

        if command in ("/stop", "/unsubscribe"):
            self.db.deactivate_chat(chat_id)
            return chat_id, "Оповещения отключены. /start — включить снова."

        if command == "/help":
            return chat_id, notify.HELP_TEXT

        if command == "/status":
            self.db.add_chat(chat_id, title)
            return chat_id, notify.status_message(
                self.monitor.interval_minutes,
                self.monitor.threshold,
                self.db.stats(),
                parse_iso(self.db.get_setting("last_poll_utc")),
                self.monitor.last_error,
            )

        if command == "/flights":
            self.db.add_chat(chat_id, title)
            rows = self.db.active_flights()
            return chat_id, notify.flights_message(
                rows[:20], self.config.include_flight_number)

        if command == "/threshold":
            return chat_id, self._set_threshold(text)

        number = _NUMBER_RE.match(text)
        if number:
            self.db.add_chat(chat_id, title)
            return chat_id, self._set_interval(int(number.group(1)))

        return chat_id, ("Не понял команду.\n\n" + notify.HELP_TEXT)

    def _set_interval(self, minutes):
        if not MIN_INTERVAL <= minutes <= MAX_INTERVAL:
            return ("Период опроса задаётся числом от %d до %d минут."
                    % (MIN_INTERVAL, MAX_INTERVAL))
        self.db.set_setting("poll_interval_minutes", minutes)
        log.info("период опроса изменён на %d мин.", minutes)
        return "Период проверки табло: %d мин. Изменения применятся к следующему циклу." % minutes

    def _set_threshold(self, text):
        parts = text.split()
        if len(parts) < 2 or not parts[1].isdigit():
            return ("Укажите порог в минутах, например: /threshold 10\n"
                    "Сейчас: %d мин." % self.monitor.threshold)
        value = int(parts[1])
        if not 1 <= value <= 600:
            return "Порог задержки задаётся числом от 1 до 600 минут."
        self.db.set_setting("min_delay_minutes", value)
        return "Оповещать о задержках от %d мин." % value


def poll_updates(client, db, handler, offset_key="telegram_offset", timeout=25):
    """Один цикл long polling: забрать обновления и ответить на них."""
    offset = db.get_setting(offset_key)
    offset = int(offset) if offset else None

    updates = client.get_updates(offset=offset, timeout=timeout)
    for update in updates or []:
        db.set_setting(offset_key, update["update_id"] + 1)
        try:
            chat_id, reply = handler.handle_update(update)
        except Exception:
            log.exception("ошибка обработки обновления %s", update.get("update_id"))
            continue
        if chat_id is not None and reply:
            try:
                client.send_message(chat_id, reply)
            except TelegramError as exc:
                log.error("не удалось ответить в чат %s: %s", chat_id, exc)
    return len(updates or [])

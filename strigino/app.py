"""Сборка приложения и главный цикл.

Работают два потока:

* ``poller``  — с заданной периодичностью читает табло, пишет в базу и
  ставит оповещения в очередь; он же отправляет накопившуюся очередь и
  раз в сутки чистит старые записи;
* ``bot``     — long polling Telegram, принимает новую периодичность и
  прочие команды.

Разделение нужно, чтобы бот отвечал мгновенно и не ждал очередного
опроса табло, а долгий сетевой запрос к сайту не блокировал команды.
"""

import logging
import signal
import sys
import threading

from . import bot as bot_module
from .db import Database
from .monitor import Monitor
from .telegram import TelegramClient, TelegramError

log = logging.getLogger("strigino.app")


def setup_logging(level="INFO", log_file=""):
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


class Application:
    def __init__(self, config):
        self.config = config
        self.db = Database(config.database)
        self.monitor = Monitor(self.db, config)
        self.client = TelegramClient(config.telegram_token) if config.telegram_token else None
        self.handler = bot_module.BotHandler(self.db, self.monitor, config)
        self.stopping = threading.Event()

    # -- отправка очереди -------------------------------------------------

    def flush_queue(self):
        """Отправить накопившиеся сообщения. Не роняет цикл при сбое сети."""
        if self.client is None:
            return 0

        sent = 0
        for message in self.db.pending_messages():
            try:
                self.client.send_message(message["chat_id"], message["text"])
                self.db.mark_sent(message["id"])
                sent += 1
            except TelegramError as exc:
                self.db.mark_failed(message["id"], exc)
                if exc.chat_gone:
                    # Бота заблокировали или чат удалён — больше туда не пишем.
                    log.warning("чат %s недоступен (%s), отключаю",
                                message["chat_id"], exc)
                    self.db.deactivate_chat(message["chat_id"])
                elif exc.fatal:
                    # Само сообщение не примут и при повторе — бросаем его,
                    # чтобы не блокировало очередь, но чат оставляем.
                    log.error("сообщение %s отклонено, пропускаю: %s",
                              message["id"], exc)
                    self.db.give_up(message["id"], exc)
                else:
                    log.error("отправка не удалась: %s", exc)
                    break  # сеть недоступна — попробуем в следующий раз
        return sent

    # -- потоки -----------------------------------------------------------

    def run_poller(self):
        while not self.stopping.is_set():
            try:
                self.monitor.poll()
                self.flush_queue()
                self.monitor.cleanup_if_due()
            except Exception:
                log.exception("сбой в цикле опроса")

            interval = max(1, self.monitor.interval_minutes) * 60
            # Ждём с проверкой флага, чтобы остановка была мгновенной, и
            # дробим сон: изменённый из бота период применится сразу.
            waited = 0
            while waited < interval and not self.stopping.is_set():
                step = min(5, interval - waited)
                if self.stopping.wait(step):
                    return
                waited += step
                interval = max(1, self.monitor.interval_minutes) * 60

    def run_bot(self):
        if self.client is None:
            log.warning("токен Telegram не задан — бот не запущен")
            return
        backoff = 1
        while not self.stopping.is_set():
            try:
                bot_module.poll_updates(self.client, self.db, self.handler)
                backoff = 1
            except TelegramError as exc:
                log.error("long polling: %s", exc)
                if self.stopping.wait(min(backoff, 60)):
                    return
                backoff = min(backoff * 2, 60)
            except Exception:
                log.exception("сбой в цикле бота")
                if self.stopping.wait(10):
                    return

    def stop(self, *_):
        log.info("останавливаюсь")
        self.stopping.set()

    def run(self):
        if self.client is not None:
            try:
                me = self.client.get_me()
                log.info("бот @%s подключён", me.get("username"))
            except TelegramError as exc:
                log.error("Telegram недоступен: %s", exc)

        for name in ("SIGINT", "SIGTERM"):
            if hasattr(signal, name):
                try:
                    signal.signal(getattr(signal, name), self.stop)
                except (ValueError, OSError):
                    pass  # не главный поток или платформа не поддерживает

        bot_thread = threading.Thread(target=self.run_bot, name="bot", daemon=True)
        bot_thread.start()
        try:
            self.run_poller()
        except KeyboardInterrupt:
            self.stop()
        finally:
            self.stopping.set()
            bot_thread.join(timeout=5)
            self.db.close()
        return 0

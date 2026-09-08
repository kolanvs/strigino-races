"""Точка входа: python3 -m strigino [команда]."""

import argparse
import os
import sys

from . import __version__, config as config_module, notify
from .app import Application, setup_logging
from .board import fetch_departures
from .db import Database
from .monitor import Monitor
from .telegram import TelegramClient, TelegramError
from .timeutil import fmt_ru, human_minutes, parse_iso

DEFAULT_CONFIG = os.environ.get("STRIGINO_CONFIG", "config.json")


def _load(args):
    return config_module.load(args.config)


def cmd_run(args):
    """Постоянная работа: опрос табло и бот."""
    config = _load(args)
    setup_logging(config.log_level, config.log_file)
    if not config.telegram_token:
        print("Внимание: telegram_token не задан — оповещения никуда не уйдут.",
              file=sys.stderr)
    return Application(config).run()


def cmd_poll(args):
    """Один цикл опроса — удобно для cron и для проверки настройки."""
    config = _load(args)
    setup_logging(config.log_level, config.log_file)
    app = Application(config)
    result = app.monitor.poll()
    sent = app.flush_queue()
    app.monitor.cleanup_if_due()
    app.db.close()
    print("Результат: %s, отправлено сообщений: %d" % (result, sent))
    return 1 if result.get("error") else 0


def cmd_board(args):
    """Показать табло как его видит парсер — без записи в базу."""
    rows = fetch_departures(include_tomorrow=not args.today_only)
    print("Рейсов: %d\n" % len(rows))
    for row in rows:
        delay = ""
        if row.delay_minutes > 0:
            delay = "  задержка %s" % human_minutes(row.delay_minutes)
        elif row.delay_minutes < 0:
            delay = "  раньше на %s" % human_minutes(-row.delay_minutes)
        print("%-16s %-34s %s -> %s  [%s]%s"
              % (row.flight_no, row.route, fmt_ru(row.scheduled),
                 fmt_ru(row.expected), row.status or "—", delay))
    return 0


def cmd_status(args):
    config = _load(args)
    db = Database(config.database)
    monitor = Monitor(db, config)
    print(notify.status_message(
        monitor.interval_minutes, monitor.threshold, db.stats(),
        parse_iso(db.get_setting("last_poll_utc"))))
    db.close()
    return 0


def cmd_cleanup(args):
    config = _load(args)
    db = Database(config.database)
    removed = db.cleanup(config.keep_days)
    print("Удалено: рейсов %(flights)d, сообщений %(messages)d" % removed)
    db.close()
    return 0


def cmd_test_telegram(args):
    """Проверить токен и отправить тестовое сообщение подписчикам."""
    config = _load(args)
    if not config.telegram_token:
        print("telegram_token не задан", file=sys.stderr)
        return 1
    client = TelegramClient(config.telegram_token)
    try:
        me = client.get_me()
    except TelegramError as exc:
        print("Telegram недоступен: %s" % exc, file=sys.stderr)
        return 1
    print("Бот: @%s" % me.get("username"))

    db = Database(config.database)
    chats = db.active_chats()
    db.close()
    if not chats:
        print("Подписчиков нет. Напишите боту /start в Telegram.")
        return 0
    for chat_id in chats:
        try:
            client.send_message(chat_id, "Проверка связи: мониторинг табло Стригино работает.")
            print("Отправлено в чат %s" % chat_id)
        except TelegramError as exc:
            print("Чат %s: %s" % (chat_id, exc), file=sys.stderr)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="strigino",
        description="Мониторинг задержек вылетов аэропорта Стригино (GOJ).")
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help="путь к config.json (по умолчанию %(default)s)")
    parser.add_argument("--version", action="version", version=__version__)

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="постоянная работа: опрос табло + бот").set_defaults(func=cmd_run)
    sub.add_parser("poll", help="один цикл опроса и отправки").set_defaults(func=cmd_poll)
    sub.add_parser("status", help="состояние базы и настроек").set_defaults(func=cmd_status)
    sub.add_parser("cleanup", help="удалить записи старше keep_days").set_defaults(func=cmd_cleanup)
    sub.add_parser("test-telegram", help="проверить бота").set_defaults(func=cmd_test_telegram)

    board = sub.add_parser("board", help="показать разобранное табло")
    board.add_argument("--today-only", action="store_true",
                       help="не запрашивать завтрашние рейсы")
    board.set_defaults(func=cmd_board)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args((argv or []) + ["run"])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

"""Конфигурация: JSON-файл плюс переопределение через переменные окружения."""

import json
import os

DEFAULTS = {
    # Telegram
    "telegram_token": "",
    "allowed_chat_ids": [],       # пусто — принимать любые чаты

    # Опрос табло
    "poll_interval_minutes": 5,   # стартовое значение, меняется из бота (2..30)
    "include_tomorrow": True,     # смотреть и завтрашние рейсы: задержки
                                  # объявляют заранее
    "http_timeout": 30,
    "http_retries": 3,
    "user_agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),

    # Правила оповещений
    "min_delay_minutes": 1,           # от какой задержки оповещать
    "departed_min_delay_minutes": 15, # когда сообщать о вылете рейса, по
                                      # которому задержку заранее не объявляли
    "include_flight_number": True,
    "show_total_delay": True,     # добавлять строку с суммарной задержкой
    "notify_cancelled": True,

    # Хранение
    "database": "strigino.db",
    "keep_days": 14,
    "log_level": "INFO",
    "log_file": "",
}

ENV_PREFIX = "STRIGINO_"


class Config:
    def __init__(self, values):
        for key, value in values.items():
            setattr(self, key, value)

    def __repr__(self):
        safe = dict(self.__dict__)
        if safe.get("telegram_token"):
            safe["telegram_token"] = "***"
        return "Config(%r)" % safe


def _coerce(default, raw):
    """Привести строку из окружения к типу значения по умолчанию."""
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on", "да")
    if isinstance(default, int):
        return int(raw)
    if isinstance(default, list):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return raw


def load(path=None):
    values = dict(DEFAULTS)

    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            file_values = json.load(handle)
        unknown = set(file_values) - set(DEFAULTS)
        if unknown:
            raise ValueError("неизвестные параметры в %s: %s"
                             % (path, ", ".join(sorted(unknown))))
        values.update(file_values)

    for key, default in DEFAULTS.items():
        raw = os.environ.get(ENV_PREFIX + key.upper())
        if raw is not None:
            values[key] = _coerce(default, raw)

    if values["database"] and path and not os.path.isabs(values["database"]):
        values["database"] = os.path.join(os.path.dirname(os.path.abspath(path)),
                                          values["database"])

    return Config(values)

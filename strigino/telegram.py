"""Минимальный клиент Telegram Bot API на стандартной библиотеке.

Никаких сторонних пакетов: на роутере с OpenWrt установка python3-requests
и зависимостей стоит места во флеш-памяти, а нужны отсюда ровно два
метода — sendMessage и getUpdates.
"""

import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("strigino.telegram")

API_URL = "https://api.telegram.org/bot%s/%s"


class TelegramError(Exception):
    """Ошибка обращения к Bot API."""

    def __init__(self, message, code=None, retry_after=None, fatal=False):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after
        self.fatal = fatal


class TelegramClient:
    def __init__(self, token, timeout=30):
        self.token = token
        self.timeout = timeout
        self._context = ssl.create_default_context()

    def _call(self, method, params=None, timeout=None):
        url = API_URL % (self.token, method)
        data = urllib.parse.urlencode(params or {}).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "strigino-races/1.0",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout,
                                        context=self._context) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except ValueError:
                raise TelegramError("HTTP %s: %s" % (exc.code, body[:200]), code=exc.code)
            description = payload.get("description", body[:200])
            retry_after = (payload.get("parameters") or {}).get("retry_after")
            # 400/403 — сообщение не примут и при повторе (чат удалён, бот
            # заблокирован), поэтому такие ошибки помечаем фатальными.
            fatal = exc.code in (400, 403)
            raise TelegramError(description, code=exc.code,
                                retry_after=retry_after, fatal=fatal)
        except (urllib.error.URLError, OSError, ssl.SSLError, ValueError) as exc:
            raise TelegramError(str(exc))

        if not payload.get("ok"):
            raise TelegramError(payload.get("description", "неизвестная ошибка"))
        return payload.get("result")

    def get_me(self):
        return self._call("getMe")

    def send_message(self, chat_id, text, disable_notification=False):
        return self._call("sendMessage", {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
            "disable_notification": "true" if disable_notification else "false",
        })

    def get_updates(self, offset=None, timeout=25):
        """Long polling. Сетевой таймаут берётся с запасом над серверным."""
        params = {"timeout": timeout, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", params, timeout=timeout + 15)

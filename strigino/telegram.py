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
    """Ошибка обращения к Bot API.

    Различаются три исхода, потому что реакция на них разная:

    * ``chat_gone`` — чат недоступен навсегда (бота заблокировали, чат
      удалили): подписку надо отключить;
    * ``fatal`` — это конкретное сообщение не примут и при повторе
      (например, испорченная HTML-разметка): его надо бросить, но чат
      трогать нельзя;
    * остальное — временное (сеть, 5xx, 429): повторить позже.
    """

    def __init__(self, message, code=None, retry_after=None,
                 fatal=False, chat_gone=False):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after
        self.fatal = fatal or chat_gone
        self.chat_gone = chat_gone


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
            lowered = description.lower()
            # 403 — бота заблокировали или выгнали из чата. Среди 400 чат
            # недоступен только по конкретным формулировкам; все прочие 400
            # (чаще всего испорченная разметка) относятся к сообщению, и
            # отписывать из-за них чат нельзя.
            chat_gone = exc.code == 403 or any(
                marker in lowered for marker in
                ("chat not found", "chat_id is empty", "user is deactivated",
                 "bot was kicked", "group chat was upgraded"))
            raise TelegramError(description, code=exc.code,
                                retry_after=retry_after,
                                fatal=exc.code == 400, chat_gone=chat_gone)
        except (urllib.error.URLError, OSError, ssl.SSLError, ValueError) as exc:
            raise TelegramError(str(exc))

        if not payload.get("ok"):
            raise TelegramError(payload.get("description", "неизвестная ошибка"))
        return payload.get("result")

    def get_me(self):
        return self._call("getMe")

    def send_message(self, chat_id, text, disable_notification=False,
                     parse_mode="HTML"):
        params = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
            "disable_notification": "true" if disable_notification else "false",
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        return self._call("sendMessage", params)

    def get_updates(self, offset=None, timeout=25):
        """Long polling. Сетевой таймаут берётся с запасом над серверным."""
        params = {"timeout": timeout, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", params, timeout=timeout + 15)

"""Загрузка и разбор онлайн-табло аэропорта Стригино.

Табло отдаётся сервером уже отрендеренным (Bitrix, без JS-подгрузки),
поэтому достаточно обычного HTTP-запроса и разбора HTML.

Разметка строки рейса:

    <a href="/board/dep-zf-6060990/" class="table-flex__row table-flex__row--link ...">
      <div class="...--type1"> 02:05 / 02.09 </div>     время по расписанию
      <div class="...--type2"> ZF-443 </div>            номер рейса
      <div class="...--type3"> Azur Air </div>          авиакомпания
      <div class="...--type4"> Анталья / AYT </div>     направление
      <div class="...--type5"> Вылетел </div>           статус
      <div class="...--type6"> 02:32 / 02.09 </div>     ожидаемое / фактическое время
      <div class="...--type7"> Совмещен c N4-829 </div> примечание
    </a>

Задержка определяется сравнением type6 с type1, а не текстом статуса:
у невылетевшего рейса без задержки эти времена совпадают, а набор
статусов на сайте меняется и на него завязываться ненадёжно.
"""

import gzip
import html
import re
import ssl
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from datetime import datetime

from .timeutil import parse_board_datetime

BASE_URL = "https://ar-goj.ru/board/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

ORIGIN_NAME = "Нижний Новгород"
ORIGIN_IATA = "GOJ"

_ROW_RE = re.compile(
    r'<a\s+href="(?P<href>/board/(?P<kind>dep|arr)-(?P<code>[a-z0-9]+)-(?P<uid>\d+)/[^"]*)"'
    r'[^>]*class="[^"]*table-flex__row--link[^"]*"[^>]*>(?P<body>.*?)</a>',
    re.S | re.I,
)
_TD_SPLIT_RE = re.compile(
    r'<div\s+class="table-flex__td\s+table-flex__td--type(\d)"[^>]*>', re.I
)
_TEXT_RE = re.compile(r'class="board__text">([^<]*)<', re.I)
_EXTRA_RE = re.compile(r'class="board__text-extra">([^<]*)<', re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(raw):
    """Снять теги и схлопнуть пробелы, включая &nbsp;."""
    text = _TAG_RE.sub(" ", raw or "")
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _split_cells(body):
    """Разложить строку таблицы по номерам колонок."""
    parts = _TD_SPLIT_RE.split(body)
    cells = {}
    for i in range(1, len(parts) - 1, 2):
        cells[int(parts[i])] = parts[i + 1]
    return cells


def _pair(cell):
    """Из колонки времени достать ("HH:MM", "DD.MM")."""
    if not cell:
        return "", ""
    time_m = _TEXT_RE.search(cell)
    date_m = _EXTRA_RE.search(cell)
    return (
        _clean(time_m.group(1)) if time_m else "",
        _clean(date_m.group(1)) if date_m else "",
    )


def _destination(cell):
    """Название и IATA пункта назначения (у рейса с посадкой их несколько)."""
    if not cell:
        return "", ""
    names = [n for n in (_clean(x) for x in _TEXT_RE.findall(cell)) if n]
    codes = [c for c in (_clean(x) for x in _EXTRA_RE.findall(cell)) if c]
    return " - ".join(names), " - ".join(codes)


def _airline(cell):
    if not cell:
        return ""
    alt = re.search(r'class="table-aircompany-logo-alt">([^<]*)<', cell, re.I)
    if alt and _clean(alt.group(1)):
        return _clean(alt.group(1))
    title = re.search(r'title="([^"]*)"', cell, re.I)
    return _clean(title.group(1)) if title else ""


@dataclass
class BoardRow:
    """Одна строка табло."""

    kind: str            # dep | arr
    site_uid: str        # 6060990 — идентификатор рейса на сайте
    flight_no: str       # ZF-443
    airline: str
    dest_name: str
    dest_iata: str
    scheduled: datetime  # время по расписанию (MSK)
    expected: datetime   # ожидаемое / фактическое время (MSK)
    status: str
    note: str
    href: str

    @property
    def leg_key(self):
        """Ключ физического рейса.

        Codeshare выводится двумя строками с одним site_uid и одним
        направлением (EO-829 и N4-829 в Уфу) — это один самолёт, и
        оповещать по нему нужно один раз, поэтому номер рейса в ключ
        не входит. А рейс с посадкой (один site_uid, разные направления)
        даёт разные плечи — они различаются по IATA.
        """
        return "%s-%s-%s" % (self.kind, self.site_uid, self.dest_iata)

    @property
    def delay_minutes(self):
        """Отклонение от расписания в минутах (отрицательное — вылет раньше)."""
        if not self.scheduled or not self.expected:
            return 0
        return int((self.expected - self.scheduled).total_seconds() // 60)

    @property
    def is_departed(self):
        status = self.status.lower()
        return any(word in status for word in ("вылетел", "departed", "прибыл", "arrived"))

    @property
    def is_cancelled(self):
        status = self.status.lower()
        return "отмен" in status or "cancel" in status

    @property
    def route(self):
        """"Нижний Новгород-Анталья (GOJ-AYT)"."""
        if self.kind == "arr":
            names = "%s-%s" % (self.dest_name, ORIGIN_NAME)
            codes = "%s-%s" % (self.dest_iata, ORIGIN_IATA)
        else:
            names = "%s-%s" % (ORIGIN_NAME, self.dest_name)
            codes = "%s-%s" % (ORIGIN_IATA, self.dest_iata)
        return "%s (%s)" % (names, codes)


def parse_board(source, reference=None):
    """Разобрать HTML табло в список BoardRow."""
    rows = []
    for match in _ROW_RE.finditer(source):
        cells = _split_cells(match.group("body"))
        sched_time, sched_date = _pair(cells.get(1))
        exp_time, exp_date = _pair(cells.get(6))

        scheduled = parse_board_datetime(sched_date, sched_time, reference)
        if scheduled is None:
            continue  # строка без времени — не рейс
        expected = parse_board_datetime(exp_date, exp_time, reference)
        if expected is None:
            expected = scheduled

        dest_name, dest_iata = _destination(cells.get(4))
        rows.append(
            BoardRow(
                kind=match.group("kind"),
                site_uid=match.group("uid"),
                flight_no=_clean(cells.get(2, "")),
                airline=_airline(cells.get(3)),
                dest_name=dest_name,
                dest_iata=dest_iata,
                scheduled=scheduled,
                expected=expected,
                status=_clean(cells.get(5, "")),
                note=_clean(cells.get(7, "")),
                href=match.group("href"),
            )
        )
    return rows


def merge_codeshares(rows):
    """Схлопнуть codeshare-строки в один рейс с объединённым номером."""
    merged = {}
    order = []
    for row in rows:
        existing = merged.get(row.leg_key)
        if existing is None:
            merged[row.leg_key] = row
            order.append(row.leg_key)
            continue
        numbers = existing.flight_no.split(" / ")
        if row.flight_no and row.flight_no not in numbers:
            existing.flight_no = " / ".join(numbers + [row.flight_no])
        if not existing.note and row.note:
            existing.note = row.note
    return [merged[key] for key in order]


class BoardError(Exception):
    pass


def fetch(url=BASE_URL, params=None, timeout=30, retries=3, user_agent=USER_AGENT):
    """Скачать страницу табло.

    Перед сайтом стоит DDoS-Guard, но с обычным браузерным User-Agent
    он пропускает запросы без челленджа и без заметного рейт-лимита.
    """
    if params:
        url = url + "?" + "&".join("%s=%s" % (k, v) for k, v in params.items())

    last_error = None
    for _ in range(max(1, retries)):
        request = urllib.request.Request(url, headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "close",
        })
        try:
            context = ssl.create_default_context()
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                raw = response.read()
                encoding = (response.headers.get("Content-Encoding") or "").lower()
            if encoding == "gzip":
                raw = gzip.decompress(raw)
            elif encoding == "deflate":
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            return raw.decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, ssl.SSLError, EOFError, zlib.error) as exc:
            last_error = exc
    raise BoardError("не удалось загрузить %s: %s" % (url, last_error))


def fetch_departures(timeout=30, retries=3, include_tomorrow=True, user_agent=USER_AGENT):
    """Текущее табло вылетов (со вчерашними и, опционально, завтрашними рейсами).

    ready=yes оставляет на табло уже вылетевшие рейсы. Без него вылет
    выглядел бы как исчезновение строки, и событие "вылетел с задержкой"
    поймать было бы нельзя.
    """
    pages = [{"ready": "yes"}]
    if include_tomorrow:
        pages.append({"ready": "yes", "date": "tomorrow"})

    rows = []
    errors = []
    for params in pages:
        try:
            source = fetch(params=params, timeout=timeout, retries=retries,
                           user_agent=user_agent)
            rows.extend(parse_board(source))
        except BoardError as exc:
            errors.append(str(exc))
    if errors and not rows:
        raise BoardError("; ".join(errors))

    return merge_codeshares([row for row in rows if row.kind == "dep"])

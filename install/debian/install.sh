#!/bin/sh
# Установка мониторинга задержек вылетов Стригино на Debian/Ubuntu (systemd).
#
#   sudo ./install/debian/install.sh
#   sudo ./install/debian/install.sh --token 123456:AA...   # без вопросов
#
# Сторонних Python-пакетов не требуется — используется только стандартная
# библиотека, поэтому ни venv, ни pip здесь не нужны.

set -eu

APP_NAME="strigino-races"
APP_DIR="/opt/${APP_NAME}"
CONF_DIR="/etc/${APP_NAME}"
DATA_DIR="/var/lib/${APP_NAME}"
SERVICE="/etc/systemd/system/${APP_NAME}.service"
APP_USER="strigino"

TOKEN=""
while [ $# -gt 0 ]; do
    case "$1" in
        --token) TOKEN="${2:-}"; shift 2 ;;
        --token=*) TOKEN="${1#--token=}"; shift ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; exit 1 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "Запустите с правами root: sudo $0" >&2
    exit 1
fi

SRC_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
if [ ! -d "${SRC_DIR}/strigino" ]; then
    echo "Не найден каталог strigino рядом с установщиком (${SRC_DIR})" >&2
    exit 1
fi

echo "==> Проверяю Python"
if ! command -v python3 >/dev/null 2>&1; then
    echo "Ставлю python3"
    apt-get update -qq
    apt-get install -y --no-install-recommends python3
fi
python3 - <<'PY'
import sys
if sys.version_info < (3, 7):
    sys.exit("Нужен Python 3.7 или новее, установлен %s" % sys.version.split()[0])
import argparse, gzip, html, json, logging, re, signal, sqlite3, ssl, threading, urllib.request, zlib
from dataclasses import dataclass
print("Python %s — все нужные модули на месте" % sys.version.split()[0])
PY

echo "==> Создаю пользователя ${APP_USER}"
if ! id "${APP_USER}" >/dev/null 2>&1; then
    useradd --system --home-dir "${DATA_DIR}" --shell /usr/sbin/nologin "${APP_USER}"
fi

echo "==> Копирую файлы в ${APP_DIR}"
mkdir -p "${APP_DIR}" "${CONF_DIR}" "${DATA_DIR}"
rm -rf "${APP_DIR}/strigino"
cp -r "${SRC_DIR}/strigino" "${APP_DIR}/strigino"
[ -f "${SRC_DIR}/README.md" ] && cp "${SRC_DIR}/README.md" "${APP_DIR}/"
find "${APP_DIR}" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> Настраиваю ${CONF_DIR}/config.json"
if [ -f "${CONF_DIR}/config.json" ]; then
    echo "    конфиг уже существует, не трогаю"
else
    if [ -z "${TOKEN}" ]; then
        echo "    Токен бота выдаёт @BotFather в Telegram."
        printf "    Вставьте токен (или Enter, чтобы вписать позже): "
        read -r TOKEN || TOKEN=""
    fi
    cat > "${CONF_DIR}/config.json" <<EOF
{
    "telegram_token": "${TOKEN}",
    "allowed_chat_ids": [],
    "poll_interval_minutes": 5,
    "min_delay_minutes": 1,
    "departed_min_delay_minutes": 15,
    "database": "${DATA_DIR}/strigino.db",
    "keep_days": 14,
    "log_level": "INFO"
}
EOF
fi

chown -R root:root "${APP_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${DATA_DIR}"
chown root:"${APP_USER}" "${CONF_DIR}/config.json"
chmod 640 "${CONF_DIR}/config.json"   # в нём токен бота

echo "==> Ставлю сервис"
cp "${SRC_DIR}/install/debian/${APP_NAME}.service" "${SERVICE}"
systemctl daemon-reload
systemctl enable "${APP_NAME}"

echo "==> Проверяю связь с табло"
if ! (cd "${APP_DIR}" && sudo -u "${APP_USER}" python3 -m strigino board --today-only >/dev/null); then
    echo "    Табло сейчас недоступно — сервис всё равно поставлен и будет пытаться дальше." >&2
fi

systemctl restart "${APP_NAME}"
sleep 2
systemctl --no-pager --lines=10 status "${APP_NAME}" || true

cat <<EOF

Готово.

Дальше:
  1. Напишите своему боту в Telegram /start — чат подпишется на оповещения.
  2. Пришлите боту число от 2 до 30 — период проверки табло в минутах.

Полезное:
  журнал        journalctl -u ${APP_NAME} -f
  состояние     cd ${APP_DIR} && sudo -u ${APP_USER} python3 -m strigino --config ${CONF_DIR}/config.json status
  табло сейчас  cd ${APP_DIR} && python3 -m strigino board
  конфиг        ${CONF_DIR}/config.json  (после правки: systemctl restart ${APP_NAME})
  база          ${DATA_DIR}/strigino.db
EOF

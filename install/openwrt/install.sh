#!/bin/sh
# Установка мониторинга задержек вылетов Стригино на роутер с OpenWrt.
# Проверялось на 25.12.3; поддерживаются и apk, и opkg.
#
#   ./install/openwrt/install.sh
#   ./install/openwrt/install.sh --token 123456:AA... --data-dir /mnt/sda1/strigino
#
# Сторонние Python-пакеты не нужны — только стандартная библиотека.
#
# ВНИМАНИЕ про флеш-память: база пишется при каждом опросе табло. Встроенная
# флеш-память роутера рассчитана на ограниченное число перезаписей, поэтому
# при постоянной работе базу лучше держать на USB-накопителе:
#   --data-dir /mnt/sda1/strigino

set -eu

APP_NAME="strigino-races"
APP_DIR="/usr/lib/${APP_NAME}"
CONF_DIR="/etc/${APP_NAME}"
DATA_DIR="/srv/${APP_NAME}"
INIT="/etc/init.d/${APP_NAME}"

TOKEN=""
while [ $# -gt 0 ]; do
    case "$1" in
        --token) TOKEN="${2:-}"; shift 2 ;;
        --token=*) TOKEN="${1#--token=}"; shift ;;
        --data-dir) DATA_DIR="${2:-}"; shift 2 ;;
        --data-dir=*) DATA_DIR="${1#--data-dir=}"; shift ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "Неизвестный аргумент: $1" >&2; exit 1 ;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo "Запустите от root" >&2; exit 1; }

SRC_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
[ -d "${SRC_DIR}/strigino" ] || {
    echo "Не найден каталог strigino рядом с установщиком (${SRC_DIR})" >&2; exit 1; }

# -- менеджер пакетов -------------------------------------------------------

if command -v apk >/dev/null 2>&1; then
    PKG="apk"
elif command -v opkg >/dev/null 2>&1; then
    PKG="opkg"
else
    echo "Не найден ни apk, ни opkg — это не OpenWrt?" >&2
    exit 1
fi
echo "==> Менеджер пакетов: ${PKG}"

pkg_update() {
    if [ "${PKG}" = "apk" ]; then apk update; else opkg update; fi
}
pkg_install() {
    # Часть пакетов может отсутствовать в конкретной сборке — это не повод падать.
    for p in "$@"; do
        if [ "${PKG}" = "apk" ]; then
            apk add --no-interactive "$p" 2>/dev/null || echo "    пропускаю ${p}"
        else
            opkg install "$p" 2>/dev/null || echo "    пропускаю ${p}"
        fi
    done
}

# -- свободное место --------------------------------------------------------

FREE_KB="$(df -k /overlay 2>/dev/null | awk 'NR==2 {print $4}')"
[ -z "${FREE_KB}" ] && FREE_KB="$(df -k / | awk 'NR==2 {print $4}')"
echo "==> Свободно в overlay: $((FREE_KB / 1024)) МБ"
if [ "${FREE_KB}" -lt 12000 ]; then
    echo "    Python со всеми модулями занимает около 12 МБ."
    echo "    Места может не хватить — при нехватке ставьте на extroot/USB."
fi

# -- Python -----------------------------------------------------------------

echo "==> Ставлю Python и нужные модули"
pkg_update
pkg_install python3-light python3-sqlite3 python3-urllib python3-openssl \
            python3-logging python3-codecs python3-email python3-ctypes \
            python3-decimal ca-bundle ca-certificates

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 не установился — проверьте место и репозитории" >&2
    exit 1
fi

echo "==> Проверяю доступность модулей"
MISSING=""
for module in argparse json sqlite3 ssl gzip zlib html logging threading signal \
              urllib.request dataclasses datetime re; do
    python3 -c "import ${module}" 2>/dev/null || MISSING="${MISSING} ${module}"
done
if [ -n "${MISSING}" ]; then
    echo "Не хватает модулей Python:${MISSING}" >&2
    echo "Попробуйте поставить полный пакет: ${PKG} install python3" >&2
    exit 1
fi
echo "    все модули на месте ($(python3 -c 'import sys;print(sys.version.split()[0])'))"

# -- файлы ------------------------------------------------------------------

echo "==> Копирую файлы в ${APP_DIR}"
mkdir -p "${APP_DIR}" "${CONF_DIR}" "${DATA_DIR}"
rm -rf "${APP_DIR}/strigino"
cp -r "${SRC_DIR}/strigino" "${APP_DIR}/strigino"
find "${APP_DIR}" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

case "${DATA_DIR}" in
    /srv/*|/root/*|/etc/*)
        echo "    База ляжет во внутреннюю флеш-память (${DATA_DIR})."
        echo "    При круглосуточной работе перенесите её на USB:"
        echo "      --data-dir /mnt/sda1/strigino"
        ;;
esac

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
    "poll_interval_minutes": 10,
    "min_delay_minutes": 1,
    "departed_min_delay_minutes": 15,
    "database": "${DATA_DIR}/strigino.db",
    "keep_days": 14,
    "log_level": "INFO"
}
EOF
fi
chmod 600 "${CONF_DIR}/config.json"   # в нём токен бота

# -- сервис -----------------------------------------------------------------

echo "==> Ставлю сервис"
cp "${SRC_DIR}/install/openwrt/${APP_NAME}.init" "${INIT}"
chmod +x "${INIT}"
"${INIT}" enable

echo "==> Проверяю связь с табло"
PYTHONPATH="${APP_DIR}" python3 -m strigino board --today-only >/dev/null 2>&1 \
    && echo "    табло читается" \
    || echo "    табло сейчас недоступно — сервис всё равно поставлен" >&2

"${INIT}" restart
sleep 3

if pgrep -f "strigino" >/dev/null 2>&1; then
    echo "    сервис работает"
else
    echo "    сервис не поднялся, смотрите: logread -e strigino" >&2
fi

cat <<EOF

Готово.

Дальше:
  1. Напишите своему боту в Telegram /start — чат подпишется на оповещения.
  2. Пришлите боту число от 2 до 30 — период проверки табло в минутах.
     На роутере разумно ставить 10-15 минут: меньше запросов и меньше
     записей во флеш-память.

Полезное:
  журнал        logread -f -e strigino
  состояние     ${INIT} status
  перезапуск    ${INIT} restart
  табло сейчас  PYTHONPATH=${APP_DIR} python3 -m strigino board
  конфиг        ${CONF_DIR}/config.json  (после правки: ${INIT} restart)
  база          ${DATA_DIR}/strigino.db
EOF

#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_SRC="${SCRIPT_DIR}/ag"

echo "=========================================================="
echo "        Установка Antigravity Linux Bypass"
echo "=========================================================="

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "[!] Ошибка: Python 3 не найден. Установите Python 3."
    exit 1
fi

chmod +x "${BIN_SRC}"

# Target binary directory
if [ "$EUID" -eq 0 ]; then
    TARGET_BIN="/usr/local/bin/ag"
else
    mkdir -p "${HOME}/.local/bin"
    TARGET_BIN="${HOME}/.local/bin/ag"
fi

ln -sf "${BIN_SRC}" "${TARGET_BIN}"
echo "[✓] Ссылка на исполняемый файл создана: ${TARGET_BIN}"

# Systemd timer setup (requires root)
if [ "$EUID" -eq 0 ]; then
    echo "[*] Настройка systemd таймера фонового мониторинга..."
    cp -f "${SCRIPT_DIR}/systemd/ag-check.service" /etc/systemd/system/
    cp -f "${SCRIPT_DIR}/systemd/ag-check.timer" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable --now ag-check.timer
    echo "[✓] Таймер ag-check.timer успешно установлен и запущен."
else
    echo ""
    echo "[i] Для установки фонового таймера мониторинга systemd запустите:"
    echo "    sudo ${SCRIPT_DIR}/install.sh"
fi

echo ""
echo "=========================================================="
echo " Установка завершена!"
echo " Команды управления:"
echo "   sudo ag on       - Включить обход и пропатчить клиент"
echo "   ag status        - Проверить текущий статус и задержку узла"
echo "   ag nodes         - Проверить и сравнить все доступные узлы"
echo "   sudo ag check    - Проверить доступность с повторами и авто-сменить IP"
echo "   sudo ag off      - Выключить обход и очистить /etc/hosts"
echo "   sudo ag unpatch  - Восстановить оригинальный бинарник"
echo "=========================================================="

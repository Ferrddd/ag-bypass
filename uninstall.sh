#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=========================================================="
echo "        Удаление Antigravity Linux Bypass"
echo "=========================================================="

# Remove entries from /etc/hosts if root
if [ "$EUID" -eq 0 ]; then
    "${SCRIPT_DIR}/ag" off 2>/dev/null || true
    "${SCRIPT_DIR}/ag" unpatch 2>/dev/null || true

    if systemctl is-active --quiet ag-check.timer 2>/dev/null; then
        systemctl disable --now ag-check.timer 2>/dev/null || true
    fi
    rm -f /etc/systemd/system/ag-check.service
    rm -f /etc/systemd/system/ag-check.timer
    systemctl daemon-reload 2>/dev/null || true
    echo "[✓] Службы systemd удалены."

    rm -f /usr/local/bin/ag
    rm -rf /var/lib/ag-bypass
    echo "[✓] Файлы из /usr/local/bin и /var/lib/ag-bypass удалены."
else
    rm -f "${HOME}/.local/bin/ag"
    rm -rf "${HOME}/.config/ag-bypass"
    echo "[✓] Симлинк из ~/.local/bin/ag удалён."
    echo "[i] Для полного удаления системных служб и очистки /etc/hosts запустите:"
    echo "    sudo ${SCRIPT_DIR}/uninstall.sh"
fi

echo "=========================================================="
echo "[✓] Удаление завершено."
echo "=========================================================="

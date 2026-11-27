# Antigravity Bypass (ag-custom) — Windows, Linux & macOS

Легковесное, полностью автономное кроссплатформенное решение для работы с **Google Antigravity** в заблокированных регионах без сторонних прокси-демонов, внешних зависимостей и привязок к HWID.

---

## ⚡ Особенности

- **0 внешних зависимостей:** Работает «из коробки» на стандартной библиотеке Python 3 (`socket`, `ssl`, `struct`, `pathlib` и т.д.). Никаких `pip install`, `virtualenv` или компиляторов.
- **Кроссплатформенность:** Нативная поддержка Windows 10/11 (`hosts`, `HKCU\Environment`, `Task Scheduler`, `ipconfig /flushdns`), Linux (`/etc/hosts`, `environment.d`, `systemd`, `resolvectl`) и macOS (`/etc/hosts`, `dscacheutil`).
- **Без локальных демонов:** Не держит в памяти постоянно работающие proxy-серверы. Система обращается к узлам обхода напрямую через быстрое SNI-проксирование.
- **Точечная маршрутизация (Split-DNS & Multi-Node):** Через `hosts` перенаправляются исключительно рабочие и авторизационные домены Antigravity с поддержкой записи топ-2 рабочих IP для мгновенного сокет-фейловера на уровне ОС (Happy Eyeballs RFC 8305).
- **SmartDNS + DoT / DoH:** Параллельно резолвит IP через обычный UDP DNS, **DoT** (DNS over TLS, порт 853) и **DoH** (DNS over HTTPS: JSON + RFC 8484). Для каждого домена в `smart_dns.servers` автоматически пробуются и DoT (`tls://<домен>`), и DoH (`https://<домен>/dns-query`), а также поддерживаются явные URL с нестандартными путями (`https://.../resolve`, `doh_servers`, `dot_servers`) и резервный fallback. Затем проверяет каждый IP через TLS 443 хэндшейк с фильтрацией ошибок прокси (502/503/403) и выбирает узлы с минимальным пингом.
- **Авто-патчинг клиента:** Находит все установленные клиенты (и CLI `agy`, и GUI `language_server` / `language_server.exe`: `Antigravity.app/Contents/Resources/bin/language_server` на macOS с переподписью `codesign`, `/opt/antigravity/resources/bin/language_server` на Linux, `resources\bin\language_server.exe` на Windows) и безопасно модифицирует проверку доступности региона (`ineligible` $\to$ `inexigible`, 10 байт в 10 байт) без создания лишних `.bak`-копий.
- **Фоновый мониторинг (Systemd Timer / Task Scheduler):** Проверяет доступность узла каждые 2 минуты (на Windows запускается бесшумно через `pythonw.exe` без всплывающих окон терминала и с правами `/rl HIGHEST`), а `ag off` / `ag on` отключают и включают мониторинг без повторной установки.

---

## 🚀 Быстрый старт

### Вариант A: Готовые архивы из Releases

В каждом релизе на GitHub доступны 2 готовых архива:

1. **Windows 10/11 (`ag-bypass-windows.zip`):**
   - Скачайте и распакуйте архив.
   - Запустите **`install.bat`** (двойным кликом — скрипт сам запросит права Администратора через UAC, или из CMD/PowerShell от Администратора):
     ```bat
     .\install.bat
     ```

2. **Linux & macOS (`ag-bypass-linux-macos.tar.gz`):**
   - Скачайте и распакуйте архив:
     ```bash
     tar -xzf ag-bypass-linux-macos.tar.gz
     ```
   - Запустите скрипт установки:
     ```bash
     # Базовая установка (создаёт ag в ~/.local/bin/ag):
     ./install.sh

     # Или системная установка с фоновым таймером systemd:
     sudo ./install.sh
     ```

---

### Вариант B: Установка из Git-репозитория

**Linux / macOS:**
```bash
git clone <repo-url> ag-custom
cd ag-custom
./install.sh       # или sudo ./install.sh
```

**Windows (PowerShell или CMD от имени Администратора):**
```bat
git clone <repo-url> ag-custom
cd ag-custom
.\install.bat
```

### 2. Включение обхода
- **Linux:** `sudo ag on`
- **Windows:** `ag on` (в консоли от Администратора) или `.\ag.bat on`
Команда:
1. Автоматически найдёт и пропатчит все найденные клиенты (`agy` CLI и/или GUI `language_server`).
2. Запросит актуальные IP у SmartDNS.
3. Проверит скорость каждого узла через TLS-рукопожатие на порту 443.
4. Добавит лучший узел в `/etc/hosts` и сбросит системный DNS-кэш (`resolvectl flush-caches`).

### 3. Проверка статуса
```bash
ag status
```
Выведет:
- Статус патча бинарника `agy`
- Текущие записи в `/etc/hosts`
- Проверку доступности активного прокси-узла и его задержку в миллисекундах
- Состояние службы фонового мониторинга

### 4. Отключение обхода
```bash
sudo ag off
```
Бесследно удаляет блок записей из `hosts`, сбрасывает кэш DNS и приостанавливает фоновый мониторинг (при следующем `ag on` он включится обратно автоматически).

---

## ⚙️ Конфигурация (`config.json`)

Вся настройка осуществляется через простой JSON-файл:

```json
{
  "smart_dns": {
    "servers": [
      "dns1.example.com",
      "1.2.3.4"
    ],
    "port": 53,
    "timeout_seconds": 3.0
  },
  "nodes": [],
  "domains": [
    "daily-cloudcode-pa.googleapis.com",
    "cloudcode-pa.googleapis.com"
  ],
  "binary_path": "auto",
  "healthcheck": {
    "interval_minutes": 2,
    "connect_timeout_seconds": 2.5,
    "probe_port": 443,
    "retries": 3,
    "retry_delay_seconds": 1.0
  }
}
```

- **`smart_dns.servers`**: Общий список серверов DNS: домены (`dns1.example.com`), IP (`1.2.3.4`), DoT (`tls://...`) или DoH (`https://...`). Для каждого обычного домена автоматически пробуются UDP 53, DoT (`tls://...`) и DoH (`https://.../dns-query`). При необходимости можно также задать отдельные поля `doh_servers`, `dot_servers` или `smart_dns.dot_port`.
- **`nodes`**: Список статических IP-адресов прокси-узлов (используются напрямую или объединяются с адресами от DNS).
- **`domains`**: Список доменов, которые направляются на прокси-узлы (рабочие эндпоинты Cloud Code).
- **`binary_path`**: `"auto"` для автопоиска в каталогах Antigravity (`AppData\Local\Programs\antigravity\resources\bin\language_server.exe` на Windows, `/Applications/Antigravity.app/Contents/Resources/bin/language_server` на macOS), `~/.local/bin/agy`, `PATH` или каталогах Windows/Linux/macOS, либо прямой путь к файлу или `.app`-бандлу.
- **`healthcheck`**: Таймауты, число повторных проверок (`retries`) и интервал запуска (`interval_minutes`).

---

## 🛠️ Справка по командам

| Команда (Linux) | Команда (Windows) | Описание |
|---|---|---|
| `sudo ag on` | `ag on` (Admin) | Включить обход: пропатчить клиент, найти лучший IP и прописать в `hosts` |
| `sudo ag off` | `ag off` (Admin) | Отключить обход: удалить записи из `hosts` и сбросить DNS-кэш |
| `ag status` | `ag status` | Показать статус бинарника, текущий узел и его задержку |
| `ag nodes` | `ag nodes` | Протестировать все доступные узлы и вывести таблицу задержек |
| `sudo ag check` | `ag check` (Admin) | Проверить доступность узла с повторами и автоматически переключить при падении (`-q` для фонового таймера) |
| `sudo ag unpatch` | `ag unpatch` (Admin) | Откатить патч (`inexigible` $\to$ `ineligible`) у найденных клиентов |

---

## 🔍 Как это работает изнутри

1. **Сетевой движок — DNS и TLS SNI (`core/network.py`):**  
   Параллельно опрашивает настроенные DNS-серверы через собственный легковесный RFC 1035 UDP-клиент без сторонних библиотек. Затем проверяет каждого кандидата через прямой TLS SNI-хэндшейк (`daily-cloudcode-pa.googleapis.com:443`) и ранжирует узлы по минимальному пингу. При сбое автоматически переключает на живой узел.
2. **Системные модификаторы (`core/system.py`, `core/system_linux.py`, `core/system_windows.py`):**  
   - **Атомарный менеджер `hosts`:** изолирует записи между тегами `# BEGIN AG_BYPASS` и `# END AG_BYPASS`, сохраняет резервную копию `.ag_bak`, производит безопасную замену и сбрасывает DNS-кэш (`resolvectl flush-caches` на Linux, `ipconfig /flushdns` на Windows).
   - **Эндпоинт `CLOUD_CODE_URL`:** настраивает переменные окружения для клиентских сессий (в `environment.d`/`profile.d` на Linux и в реестре `HKCU\Environment`/`setx` на Windows).
   - **Бинарный патчер:** безопасно заменяет сигнатуру отказа региона `ineligible` на `inexigible` (10 байт в 10 байт) сразу во всех найденных клиентах (CLI и GUI) без создания `.bak`-файлов.
3. **Конфигурация (`core/config.py`):**  
   Обеспечивает гибкий поиск конфигурации (аргумент командной строки, переменная окружения, системные и пользовательские каталоги) и валидацию настроек.

---

## 🗑️ Удаление

**Linux:**
```bash
sudo ./uninstall.sh
```

**Windows (PowerShell или CMD от Администратора):**
```bat
.\uninstall.bat
```

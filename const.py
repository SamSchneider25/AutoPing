__version__ = "2.0.0"

STATUS_ACTIVE = "active"
STATUS_DEGRADED = "degraded"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"

STATUS_TEXT = {
    STATUS_ACTIVE: "активен",
    STATUS_DEGRADED: "деградировано",
    STATUS_FAILED: "не отвечает",
    STATUS_UNKNOWN: "неизвестно",
}

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

STATUS_COLOR = {
    STATUS_ACTIVE: GREEN,
    STATUS_DEGRADED: YELLOW,
    STATUS_FAILED: RED,
    STATUS_UNKNOWN: DIM,
}

STATUS_DOT = {
    STATUS_ACTIVE: f"{GREEN}●{RESET}",
    STATUS_DEGRADED: f"{YELLOW}○{RESET}",
    STATUS_FAILED: f"{RED}✗{RESET}",
    STATUS_UNKNOWN: f"{DIM}?{RESET}",
}

TILE_ACTIVE = f"{GREEN}█{RESET}"
TILE_DEGRADED = f"{YELLOW}▓{RESET}"
TILE_FAILED = f"{RED}█{RESET}"
TILE_EMPTY = f"{DIM}░{RESET}"

HISTORY_WIDTH = 24

HELP_TEXT = f"""{BOLD}AutoPing (PingCtl) v{__version__} — шпора{RESET}

{BOLD}Флаги:{RESET}
  ХОСТ [ХОСТ ...]   Хосты/IP через пробел, ; или , (например: 1.1.1.1;8.8.8.8;ya.ru)
  -f, --file FILE   Файл со списком хостов (по одной строке, # — комментарий)
  -c, --count N     Число пакетов на хост (по умолчанию 4)
  -t, --timeout N   Таймаут ответа на пакет, мс (по умолчанию 1000)
  -i, --interval N  Интервал между пакетами, сек (Linux/macOS)
  -s, --size N      Размер пакета, байт
  -w, --deadline N  Общее время ожидания, сек (Linux)
  --port N          Порт для справки (ping/ICMP не использует порты)
  --watch N         Обновлять экран каждые N сек (режим systemctl/watch)
  --output json|csv Экспортировать результат в JSON/CSV
  --out ФАЙЛ        Куда сохранить экспорт (без этого — в stdout)
  --no-color        Выключить цвета
  -V, --version     Версия программы

{BOLD}Пример вывода:{RESET}
  ●  1.1.1.1          ████▓▓░░░░░░░░░░░░░░░░░░░░░   активен          2/2     0.0%     12.0
  ○  8.8.8.8          ████████▓░░░░░░░░░░░░░░░░░░   деградировано    1/2    50.0%     52.0   Потеря пакетов 50.0%
  ✗  bad.host         ░░░░░░░░░░░░░░░░░░░░░░░░░░   не отвечает      —/—     — %       —       Узел не найден

{BOLD}ИСТОРИЯ (тайлы):{RESET}
  █ зелёный — отвечает   █ красный — не отвечает   ▓ жёлтый — деградировано   ░ серый — нет данных

{BOLD}Коды выхода:{RESET}
  0   все хосты отвечают
  1   есть деградированные / не отвечающие
  2   ошибка аргументов или чтения файла
  130 прервано пользователем (Ctrl+C)

{BOLD}Примечание:{RESET} ping (ICMP) не работает с портами. --port только для справки.
"""

EXAMPLE_OUTPUT = """AutoPing — результаты проверки

┌ СТАТУС  ХОСТ             ИСТОРИЯ                    СТАТУС            ПАКЕТЫ   ПОТЕРЯ   СРЕДНЕ мс  ОШИБКА
└──────────────────────────────────────────────────────────────────────────────────────────────────────
●  1.1.1.1          ████▓▓░░░░░░░░░░░░░░░░░░░░░   активен          2/2     0.0%     12.0
✗  bad.host         ░░░░░░░░░░░░░░░░░░░░░░░░░░   не отвечает      —/—     — %       —       Узел не найден

Итого: 2 хостов, 1 активен, 0 деградировано, 1 не отвечает
"""

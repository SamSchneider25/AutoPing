__version__ = "2.5.0"

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

PORT_TEXT = {
    STATUS_ACTIVE: "открыт",
    STATUS_FAILED: "не доступен",
}

TILE_ACTIVE = f"{GREEN}█{RESET}"
TILE_DEGRADED = f"{YELLOW}▓{RESET}"
TILE_FAILED = f"{RED}█{RESET}"
TILE_EMPTY = f"{DIM}░{RESET}"

HISTORY_WIDTH = 24

HELP_TEXT = f"""{BOLD}AutoPing v{__version__}{RESET}  [ping|port] [хосты;...] [флаги]

{BOLD}ping:{RESET} (слово ping можно опустить)
  -c N   пакетов на хост (4)
  -t     без остановки (continuous)
  -f     flood: быстрая серия, live-потери
  -W N   таймаут пакета, мс (1000)
  -i N   интервал, сек
  -s N   размер пакета, байт
  -w N   дедлайн, сек
  --watch N     обновление экрана, сек
  --file F      хосты из файла
  --output json|csv  --out F
  --no-color  -V

{BOLD}port:{RESET} host:port ...  [-W N]

{BOLD}команды:{RESET} help  clear  exit  quit

{BOLD}вывод:{RESET} тайл на пакет: {GREEN}█{RESET} ok  {RED}█{RESET} loss  {YELLOW}▓{RESET} degrade  {DIM}░{RESET} пусто
  одиночный: -c N тайлов, live по мере прихода
  watch/-t/-f: история по пакетам

{BOLD}валидация:{RESET} IP / DNS с точкой / localhost; мусор = строка ✗, не пингуется
{BOLD}коды:{RESET} 0 ok  1 есть проблемы  2 аргументы  130 Ctrl+C
"""

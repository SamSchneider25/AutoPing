import csv
import io
import json
import os
import sys
from typing import Dict, List, Optional

from const import (
    BOLD, DIM, GREEN, RED, RESET, YELLOW,
    STATUS_ACTIVE, STATUS_COLOR, STATUS_DEGRADED, STATUS_DOT,
    STATUS_FAILED, STATUS_TEXT, STATUS_UNKNOWN,
    TILE_ACTIVE, TILE_DEGRADED, TILE_EMPTY, TILE_FAILED, HISTORY_WIDTH,
    HELP_TEXT, PORT_TEXT,
)

# Фиксированные ширины столбцов, чтобы таблица не «ездила»
HOST_WIDTH = 30


def setup_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def clear_frame(lines: int = 0) -> None:
    """Перерисовка фрейма без полной очистки экрана (убирает мигание)."""
    if lines > 0:
        import sys as _s

        _s.stdout.write(f"\x1b[{lines}A\x1b[J")
        _s.stdout.flush()
    else:
        clear_screen()


def compute_widths(results: List) -> dict:
    # Хост фиксированной ширины, остальные — по содержимому с минимумами
    w_host = HOST_WIDTH
    w_err = max(24, max((len(r.error_msg) for r in results if r.error_msg), default=24))
    return {
        "host": w_host,
        "status": 16,
        "packets": 9,
        "loss": 8,
        "avg": 9,
        "err": w_err,
        "history": HISTORY_WIDTH,
    }


def format_history(history: List[str], pad: bool = True) -> str:
    tiles = []
    for status in history[-HISTORY_WIDTH:]:
        if status == STATUS_ACTIVE:
            tiles.append(TILE_ACTIVE)
        elif status == STATUS_DEGRADED:
            tiles.append(TILE_DEGRADED)
        elif status == STATUS_FAILED:
            tiles.append(TILE_FAILED)
        else:
            tiles.append(TILE_EMPTY)
    if pad:
        while len(tiles) < HISTORY_WIDTH:
            tiles.append(TILE_EMPTY)
    return "".join(tiles)


def print_header(widths: dict, title: str = "AutoPing — результаты проверки") -> None:
    print(f"{BOLD}{title}{RESET}\n")
    print(
        f"┌ {'ST':<2}  {'HOST':<{widths['host']}}  "
        f"{'HIST':<{widths['history']}}  {'STATUS':<{widths['status']}}  "
        f"{'PKTS':<{widths['packets']}}  {'LOSS':<{widths['loss']}}  "
        f"{'AVG':<{widths['avg']}}  {'ERROR':<{widths['err']}}"
    )
    line_len = (
        2 + 2 + widths["host"] + 2 + widths["history"] + 2 + widths["status"]
        + 2 + widths["packets"] + 2 + widths["loss"] + 2 + widths["avg"] + 2 + widths["err"] + 6
    )
    print(f"└{'─' * line_len}")


def format_row(r, widths: dict, history: Optional[Dict[str, List[str]]] = None) -> str:
    # Обрезаем длинный хост с многоточием, чтобы не ломать таблицу
    host = r.host
    if len(host) > widths["host"]:
        host = host[: widths["host"] - 1] + "…"
    packets = f"{r.received}/{r.sent}" if r.sent else "—/—"
    loss = f"{r.loss_pct:.1f}%" if r.sent else "— %"
    avg = f"{r.avg_ms:.1f}" if r.avg_ms is not None else "—"
    err = r.error_msg[: widths["err"]]

    hist = ""
    if history and r.host in history:
        hist = format_history(history[r.host])
    elif getattr(r, "packets", None):
        # Одиночный прогон: тайл на каждый пакет (-c N -> N тайлов)
        hist = format_history(r.packets, pad=False)

    return (
        f"{r.dot()}  {host:<{widths['host']}}  "
        f"{hist:<{widths['history']}}  "
        f"{STATUS_COLOR.get(r.status, DIM)}{r.status_plain():<{widths['status']}}{RESET}  "
        f"{packets:<{widths['packets']}}  {loss:<{widths['loss']}}  "
        f"{avg:<{widths['avg']}}  {err:<{widths['err']}}"
    )


def print_table(results: List, widths: dict, history: Optional[Dict[str, List[str]]] = None) -> None:
    print_header(widths)
    for r in results:
        print(format_row(r, widths, history))


def print_summary(results: List) -> None:
    active = sum(1 for r in results if r.status == STATUS_ACTIVE)
    degraded = sum(1 for r in results if r.status == STATUS_DEGRADED)
    failed = sum(1 for r in results if r.status == STATUS_FAILED)
    unknown = sum(1 for r in results if r.status == STATUS_UNKNOWN)
    print()
    print(
        f"Итого: {len(results)} хостов, "
        f"{GREEN}{active} активен{RESET}, "
        f"{YELLOW}{degraded} деградировано{RESET}, "
        f"{RED}{failed} не отвечает{RESET}"
        + (f", {DIM}{unknown} неизвестно{RESET}" if unknown else "")
    )


def print_help_screen() -> None:
    """Краткая шпора."""
    print(HELP_TEXT)


def print_exit_summary(results: List) -> None:
    """Краткая сводка при принудительном завершении."""
    if not results:
        print("Нет данных для сводки.")
        return
    active = sum(1 for r in results if r.status == STATUS_ACTIVE)
    degraded = sum(1 for r in results if r.status == STATUS_DEGRADED)
    failed = sum(1 for r in results if r.status == STATUS_FAILED)
    unknown = sum(1 for r in results if r.status == STATUS_UNKNOWN)
    print(f"{BOLD}Итоговая сводка:{RESET}")
    print(f"  Всего хостов: {len(results)}")
    print(f"  {GREEN}Активно:{RESET} {active}")
    print(f"  {YELLOW}Деградировано:{RESET} {degraded}")
    print(f"  {RED}Не отвечает:{RESET} {failed}")
    if unknown:
        print(f"  {DIM}Неизвестно:{RESET} {unknown}")
    print()


def print_port_table(results: List, title: str = "AutoPing — проверка портов (TCP)") -> None:
    w_hp = min(34, max(24, max((len(r.label()) for r in results), default=24)))
    w_err = max(28, max((len(r.error_msg) for r in results if r.error_msg), default=28))
    print(f"{BOLD}{title}{RESET}\n")
    print(
        f"┌ {'СТАТУС':<2}  {'ХОСТ:ПОРТ':<{w_hp}}  {'СТАТУС':<14}  "
        f"{'ВРЕМЯ мс':<9}  ОШИБКА"
    )
    line_len = 2 + 2 + w_hp + 2 + 14 + 2 + 9 + 2 + w_err + 6
    print(f"└{'─' * line_len}")
    for r in results:
        hp = r.label()
        if len(hp) > w_hp:
            hp = hp[: w_hp - 1] + "…"
        t = f"{r.time_ms:.1f}" if r.time_ms is not None else "—"
        color = GREEN if r.status == STATUS_ACTIVE else RED
        text = PORT_TEXT.get(r.status, PORT_TEXT[STATUS_FAILED])
        dot = STATUS_DOT.get(r.status, STATUS_DOT[STATUS_FAILED])
        print(
            f"{dot}  {hp:<{w_hp}}  {color}{text:<14}{RESET}  "
            f"{t:<9}  {r.error_msg[:w_err]}"
        )


def print_port_summary(results: List) -> None:
    opened = sum(1 for r in results if r.status == STATUS_ACTIVE)
    closed = len(results) - opened
    print()
    print(
        f"Итого: {len(results)} портов, "
        f"{GREEN}{opened} открыто{RESET}, {RED}{closed} не доступно{RESET}"
    )


def export_results(results: List, fmt: str, path: Optional[str] = None) -> None:
    data = [r.to_dict() for r in results]
    if fmt == "json":
        text = json.dumps(data, ensure_ascii=False, indent=2)
    elif fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=[
                "host", "status", "status_text", "sent", "received",
                "loss_pct", "min_ms", "avg_ms", "max_ms", "error_code", "error_msg",
            ],
        )
        writer.writeheader()
        writer.writerows(data)
        text = buf.getvalue()
    else:
        return

    if path:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        print(text)

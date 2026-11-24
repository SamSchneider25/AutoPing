import csv
import io
import json
import os
import re
import shutil
import sys
from typing import Dict, List, Optional

from const import (
    BOLD, DIM, GREEN, RED, RESET, YELLOW,
    STATUS_ACTIVE, STATUS_COLOR, STATUS_DEGRADED, STATUS_DOT,
    STATUS_FAILED, STATUS_TEXT, STATUS_UNKNOWN,
    TILE_ACTIVE, TILE_DEGRADED, TILE_EMPTY, TILE_FAILED, HISTORY_WIDTH,
    HELP_TEXT, PORT_TEXT,
)

# Ширина столбцов фиксированная — таблица не ездит между кадрами
HOST_WIDTH = 30

_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;:?<=>]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_ESC_RE = re.compile(r"\x1b[()][A-Z0-9]|\x1b[=>]")
_ANSI_RE = re.compile(
    r"\x1b(?:\[[0-9;:?<=>]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][A-Z0-9]|\x1b[=>])"
)

# Высота прошлого кадра и ширина терминала, на которой он рисовался
_prev_h = 0
_last_cols: Optional[int] = None
_vt_ok = True


def enable_vt() -> bool:
    """Включаем ANSI в Windows-консоли; False, если режим не включился."""
    global _vt_ok
    _vt_ok = False
    if os.name != "nt":
        _vt_ok = True
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        hStdOut = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong(0)
        if kernel32.GetConsoleMode(hStdOut, ctypes.byref(mode)):
            new_mode = mode.value | 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            if kernel32.SetConsoleMode(hStdOut, new_mode):
                mode.value = new_mode
                _vt_ok = True
                return True
    except Exception:
        pass
    return False


def setup_encoding() -> None:
    enable_vt()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def clear_screen() -> None:
    """Чистим экран перед стартом live и при resize; счётчик кадра сбрасываем."""
    global _prev_h, _last_cols
    if _vt_ok and sys.stdout.isatty():
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.flush()
    else:
        os.system("cls" if os.name == "nt" else "clear")
    _prev_h = 0
    _last_cols = shutil.get_terminal_size().columns


def hide_cursor() -> None:
    """Курсор на время live-режима убираем с экрана."""
    if _vt_ok and sys.stdout.isatty():
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()


def show_cursor() -> None:
    """Возвращаем курсор при выходе из live-режима."""
    if _vt_ok and sys.stdout.isatty():
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def reset_frame() -> None:
    """Забыть высоту прошлого кадра — следующий рисуем с чистого листа."""
    global _prev_h
    _prev_h = 0


def _fit_line(s: str, cols: int) -> str:
    """Подгоняем строку под ширину терминала: ANSI-коды в ширину не считаем."""
    out: List[str] = []
    plain = 0
    i = 0
    n = len(s)
    while i < n and plain < cols:
        ch = s[i]
        if ch == "\x1b":
            m = _ANSI_CSI_RE.match(s, i) or _ANSI_OSC_RE.match(s, i) or _ANSI_ESC_RE.match(s, i)
            if m:
                out.append(m.group())
                i = m.end()
                continue
            i += 1
            continue
        out.append(ch)
        plain += 1
        i += 1
    fitted = "".join(out)
    visible = len(_ANSI_RE.sub("", fitted))
    if visible < cols:
        fitted = fitted + " " * (cols - visible)
    return fitted


def render_frame(frame: str) -> None:
    """Один кадр — один write: поднять курсор, стереть хвост, записать кадр."""
    global _prev_h, _last_cols
    if not _vt_ok or not sys.stdout.isatty():
        # Без VT просто дописываем кадры: ESC-коды были бы мусором
        sys.stdout.write(frame + "\n")
        sys.stdout.flush()
        return
    cols = shutil.get_terminal_size().columns
    full_clear = _last_cols is None or cols != _last_cols
    if full_clear:
        # Старт и resize: чистый экран, высоту кадра сбрасываем
        clear_screen()
    lines = frame.split("\n")
    buf = []
    if _prev_h > 0 and not full_clear:
        buf.append(f"\x1b[{_prev_h}A")
    buf.append("\x1b[J")
    buf.append("\n".join(_fit_line(ln, cols) for ln in lines))
    buf.append("\n")
    sys.stdout.write("".join(buf))
    sys.stdout.flush()
    _prev_h = len(lines)


def compute_widths(results: List) -> dict:
    # Хост фиксированной ширины, остальные — по содержимому, но не уже минимума
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


def table_lines(
    results: List, widths: dict, history: Optional[Dict[str, List[str]]] = None,
    title: str = "AutoPing — результаты проверки",
) -> List[str]:
    """Шапка и строки таблицы обычным текстом — красить будем после подгонки ширины."""
    lines = [
        f"{BOLD}{title}{RESET}",
        "",
        f"┌ {'ST':<2}  {'HOST':<{widths['host']}}  "
        f"{'HIST':<{widths['history']}}  {'STATUS':<{widths['status']}}  "
        f"{'PKTS':<{widths['packets']}}  {'LOSS':<{widths['loss']}}  "
        f"{'AVG':<{widths['avg']}}  {'ERROR':<{widths['err']}}",
        f"└{'─' * (
            2 + 2 + widths["host"] + 2 + widths["history"] + 2 + widths["status"]
            + 2 + widths["packets"] + 2 + widths["loss"] + 2 + widths["avg"] + 2 + widths["err"] + 6
        )}",
    ]
    for r in results:
        lines.append(format_row(r, widths, history))
    return lines


def print_header(widths: dict, title: str = "AutoPing — результаты проверки") -> int:
    for ln in table_lines([], widths, None, title)[:4]:
        print(ln)
    # Заголовок, пустая строка, шапка, разделитель
    return 4


def format_row(r, widths: dict, history: Optional[Dict[str, List[str]]] = None) -> str:
    # Длинный хост укорачиваем многоточием, чтобы не ломать колонки
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
        # В одиночном прогоне тайл на каждый пакет
        hist = format_history(r.packets, pad=False)

    return (
        f"{r.dot()}  {host:<{widths['host']}}  "
        f"{hist:<{widths['history']}}  "
        f"{STATUS_COLOR.get(r.status, DIM)}{r.status_plain():<{widths['status']}}{RESET}  "
        f"{packets:<{widths['packets']}}  {loss:<{widths['loss']}}  "
        f"{avg:<{widths['avg']}}  {err:<{widths['err']}}"
    )


def print_table(results: List, widths: dict, history: Optional[Dict[str, List[str]]] = None) -> int:
    lines = table_lines(results, widths, history)
    for ln in lines:
        print(ln)
    # Шапка плюс все строки таблицы
    return len(lines)


def summary_line(results: List) -> str:
    active = sum(1 for r in results if r.status == STATUS_ACTIVE)
    degraded = sum(1 for r in results if r.status == STATUS_DEGRADED)
    failed = sum(1 for r in results if r.status == STATUS_FAILED)
    unknown = sum(1 for r in results if r.status == STATUS_UNKNOWN)
    return (
        f"Итого: {len(results)} хостов, "
        f"{GREEN}{active} активен{RESET}, "
        f"{YELLOW}{degraded} деградировано{RESET}, "
        f"{RED}{failed} не отвечает{RESET}"
        + (f", {DIM}{unknown} неизвестно{RESET}" if unknown else "")
    )


def print_summary(results: List) -> int:
    print()
    print(summary_line(results))
    # Пустая строка и итог
    return 2


def print_help_screen() -> None:
    """Памятка команд."""
    print(HELP_TEXT)


def print_exit_summary(results: List) -> None:
    """Сводка при выходе из live-режима."""
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

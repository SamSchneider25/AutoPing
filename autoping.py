#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoPing (PingCtl) — точка входа.

Синтаксис: [команда] [хосты через ; , пробел] [флаги]
  ping 1.1.1.1;8.8.8.8 -c 2 -t 1000   (слово ping можно опустить)
  port 10.15.20.205:1501 --timeout 500
  help / clear / exit / quit
"""
import argparse
import shlex
import signal
import sys
import time
from typing import List, Optional, Tuple

from const import __version__, RED, GREEN, YELLOW, CYAN, BOLD, DIM, RESET, STATUS_FAILED
from display import (
    setup_encoding, clear_screen, compute_widths, print_table,
    print_summary, export_results, print_help_screen, print_exit_summary,
    print_port_table, print_port_summary, render_frame, hide_cursor, show_cursor,
    reset_frame, table_lines, summary_line,
)
from ping_core import (
    read_hosts_from_file, split_hosts, run_once, is_valid_host, PingResult, stream_ping,
)
from port_core import parse_port_token, run_ports

FLAG_WITH_VALUE = {
    "--file", "-c", "--count", "--timeout", "-W", "-i", "--interval",
    "-s", "--size", "-w", "--deadline", "--watch", "--output", "--out",
}
FLAG_TOKENS = FLAG_WITH_VALUE | {
    "-t", "--continuous", "-f", "--flood", "--no-color", "-V", "--version",
}


class WatchController:
    def __init__(self) -> None:
        self.running = True

    def stop(self, *args) -> None:
        self.running = False


def watch_loop(hosts, args, interval: float, controller: WatchController) -> int:
    from const import HISTORY_WIDTH

    signal.signal(signal.SIGINT, controller.stop)
    history: dict = {}
    results = []
    try:
        clear_screen()
        hide_cursor()
        while controller.running:
            results = run_once(
                hosts, args.count, args.timeout, args.interval, args.size, args.deadline
            )
            for r in results:
                history.setdefault(r.host, []).extend(r.packets or [r.status])
                history[r.host] = history[r.host][-HISTORY_WIDTH:]
            widths = compute_widths(results)
            lines = [
                f"AutoPing — режим наблюдения (обновление каждые {interval} с)",
                "",
                f"{time.strftime('%H:%M:%S')}  Ctrl+C для возврата",
                "",
            ]
            lines += table_lines(results, widths, history=history)
            lines += [
                "",
                summary_line(results),
                "",
                f"Следующее обновление через {interval} с...",
            ]
            render_frame("\n".join(lines))
            if controller.running:
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        show_cursor()
        reset_frame()
        clear_screen()
        print_exit_summary(results)
    return 0


def continuous_loop(hosts, args, controller: WatchController) -> int:
    """Бесконечный пинг (как ping -t)."""
    from const import HISTORY_WIDTH

    signal.signal(signal.SIGINT, controller.stop)
    history: dict = {}
    results = []
    interval = max(1.0, args.interval or 1.0)
    try:
        clear_screen()
        hide_cursor()
        while controller.running:
            results = run_once(
                hosts, 1, args.timeout, args.interval, args.size, args.deadline
            )
            for r in results:
                history.setdefault(r.host, []).extend(r.packets or [r.status])
                history[r.host] = history[r.host][-HISTORY_WIDTH:]
            widths = compute_widths(results)
            lines = [
                "AutoPing — continuous mode (Ctrl+C для остановки)",
                "",
                f"{time.strftime('%H:%M:%S')}  Интервал: {interval} с",
                "",
            ]
            lines += table_lines(results, widths, history=history)
            lines += [
                "",
                summary_line(results),
                "",
                f"Следующий такт через {interval} с...",
            ]
            render_frame("\n".join(lines))
            if controller.running:
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        show_cursor()
        reset_frame()
        clear_screen()
        print_exit_summary(results)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoping",
        description="Массовый пинг хостов в стиле systemctl status.",
        epilog=(
            "Примеры:\n"
            "  %(prog)s ping 1.1.1.1;8.8.8.8;ya.ru -c 2\n"
            "  %(prog)s -t 1.1.1.1 8.8.8.8           (continuous, без остановки)\n"
            "  %(prog)s port 10.15.20.205:1501 --timeout 500\n"
            "  %(prog)s --file hosts.txt --count 10 --size 64\n"
            "  %(prog)s --output json --out results.json 1.1.1.1 google.com\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("hosts", nargs="*", help="Хосты/IP (можно через ; , пробел)")
    parser.add_argument("-c", "--count", type=int, default=4, help="Число эхо-запросов на хост")
    parser.add_argument("-t", "--continuous", action="store_true", help="Пинговать без остановки (как ping -t)")
    parser.add_argument("-f", "--flood", action="store_true", help="Flood ping: быстрая серия, live-вывод потерь")
    parser.add_argument("-W", "--timeout", type=int, default=1000, help="Таймаут ответа на пакет, мс (по умолчанию 1000)")
    parser.add_argument("--file", type=str, default=None, help="Путь к файлу со списком хостов")
    parser.add_argument("-i", "--interval", type=float, default=None, help="Интервал между пакетами, сек (Linux/macOS)")
    parser.add_argument("-s", "--size", type=int, default=None, help="Размер пакета, байт")
    parser.add_argument("-w", "--deadline", type=int, default=None, help="Общее время ожидания, сек (Linux)")
    parser.add_argument("--watch", type=float, default=None, help="Перерисовывать экран каждые N секунд")
    parser.add_argument("--output", type=str, choices=["json", "csv"], default=None, help="Экспорт в JSON или CSV")
    parser.add_argument("--out", type=str, default=None, help="Путь к файлу для экспорта")
    parser.add_argument("--no-color", action="store_true", help="Отключить цвета")
    return parser


def collect_hosts(args) -> List[str]:
    hosts: List[str] = []
    for h in args.hosts:
        hosts.extend(split_hosts(h))
    if args.file:
        try:
            hosts += read_hosts_from_file(args.file)
        except FileNotFoundError:
            print(f"Ошибка: файл не найден: {args.file}", file=sys.stderr)
            sys.exit(2)
        except OSError as exc:
            print(f"Ошибка: не удалось прочитать файл: {exc}", file=sys.stderr)
            sys.exit(2)

    seen = set()
    unique: List[str] = []
    for h in hosts:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return unique


def apply_no_color() -> None:
    import const
    const.RED = const.GREEN = const.YELLOW = const.CYAN = const.BOLD = const.DIM = const.RESET = ""
    const.STATUS_DOT = {
        const.STATUS_ACTIVE: "●",
        const.STATUS_DEGRADED: "○",
        const.STATUS_FAILED: "✗",
        const.STATUS_UNKNOWN: "?",
    }
    const.TILE_ACTIVE = "█"
    const.TILE_DEGRADED = "▓"
    const.TILE_FAILED = "█"
    const.TILE_EMPTY = "░"


def _tile(status: str) -> None:
    import const
    mapping = {
        const.STATUS_ACTIVE: const.TILE_ACTIVE,
        const.STATUS_DEGRADED: const.TILE_DEGRADED,
        const.STATUS_FAILED: const.TILE_FAILED,
    }
    print(mapping.get(status, const.TILE_EMPTY), end="", flush=True)


def live_run(hosts: List[str], args, flood: bool = False) -> List[PingResult]:
    """Последовательный пинг с live-тайлами по мере прихода пакетов (промежуточное состояние)."""
    import const
    count = args.count
    if flood and args.count == 4:
        count = 100  # flood по умолчанию — 100 пакетов
    results: List[PingResult] = []
    for h in hosts:
        print(f"{const.BOLD}{h}{const.RESET}  ", end="", flush=True)
        try:
            r = stream_ping(
                h, count, args.timeout, args.interval, args.size, args.deadline,
                flood=flood, on_packet=_tile,
            )
        except KeyboardInterrupt:
            print()
            raise
        print()
        results.append(r)
    return results


def run_cli(argv: Optional[List[str]] = None) -> int:
    """Основная логика ping-режима. Возвращает код выхода."""
    setup_encoding()
    argv = preprocess_timeout(list(argv or []))
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.no_color:
        apply_no_color()

    hosts = collect_hosts(args)
    if not hosts:
        print_help_screen()
        return 2

    valid = [h for h in hosts if is_valid_host(h)]
    invalid = [h for h in hosts if not is_valid_host(h)]

    try:
        if args.continuous or args.watch:
            if invalid:
                print(f"{YELLOW}Пропущены некорректные адреса:{RESET} {', '.join(invalid)}")
            if not valid:
                print(f"{RED}Ошибка:{RESET} нет валидных хостов для пинга")
                return 2
            if args.continuous:
                return continuous_loop(valid, args, WatchController())
            return watch_loop(valid, args, max(0.5, args.watch), WatchController())

        if valid:
            if args.output:
                results = run_once(valid, args.count, args.timeout, args.interval, args.size, args.deadline)
            else:
                results = live_run(valid, args, flood=args.flood)
        else:
            results = []
        if invalid:
            # Невалидные токены — отдельными строками, вывод не пропускается
            by_host = {r.host: r for r in results}
            results = [
                by_host[h] if h in by_host else PingResult(
                    host=h, status=STATUS_FAILED,
                    error_msg="Некорректный адрес (ожидается IP или DNS-имя)",
                )
                for h in hosts
            ]
        widths = compute_widths(results)

        if args.output:
            export_results(results, args.output, args.out)
        else:
            print_table(results, widths)
            print_summary(results)

        failed = sum(1 for r in results if r.status in ("failed", "unknown"))
        degraded = sum(1 for r in results if r.status == "degraded")
        return 1 if (failed or degraded) else 0
    except KeyboardInterrupt:
        clear_screen()
        print("\nПрервано пользователем (Ctrl+C).\n")
        return 130


def preprocess_timeout(argv: List[str]) -> List[str]:
    """-t N -> --timeout N (привычный синтаксис таймаута). -t без числа = continuous."""
    out: List[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "-t" and i + 1 < len(argv) and argv[i + 1].isdigit():
            out.append("--timeout")
            out.append(argv[i + 1])
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return out


def split_hosts_flags(argv: List[str]) -> Tuple[List[str], List[str]]:
    host_args: List[str] = []
    flag_args: List[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in FLAG_TOKENS:
            flag_args.append(tok)
            if tok in FLAG_WITH_VALUE and i + 1 < len(argv):
                flag_args.append(argv[i + 1])
                i += 2
            else:
                i += 1
        else:
            host_args.append(tok)
            i += 1
    return host_args, flag_args


def do_ping(rest: List[str]) -> int:
    host_args, flag_args = split_hosts_flags(rest)
    if not host_args and not any(a in ("-f", "--file") for a in flag_args):
        print(f"{RED}Ошибка:{RESET} укажите хосты, например: ping 1.1.1.1;ya.ru -c 2")
        return 2
    try:
        return run_cli(host_args + flag_args)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0
        if code != 0:
            print(f"Ошибка аргументов (код {code})")
        return code


def do_port(rest: List[str]) -> int:
    timeout = 1000
    tokens: List[str] = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--timeout", "-t"):
            if i + 1 < len(rest) and rest[i + 1].isdigit():
                timeout = int(rest[i + 1])
                i += 2
                continue
            print(f"{RED}Ошибка:{RESET} --timeout требует число (мс)")
            return 2
        if a.startswith("-"):
            print(f"{RED}Ошибка:{RESET} неизвестный флаг для port: {a}")
            return 2
        tokens.extend(split_hosts(a))
        i += 1

    if not tokens:
        print(f"{RED}Ошибка:{RESET} укажите host:port, например: port 10.15.20.205:1501")
        return 2
    results = run_ports(tokens, timeout)
    print_port_table(results)
    print_port_summary(results)
    return 0 if all(r.status == "active" for r in results) else 1


def interactive_mode() -> int:
    setup_encoding()
    print(f"{BOLD}AutoPing (PingCtl) v{__version__} — интерактивный режим{RESET}")
    print("Синтаксис: [команда] [хосты через ;] [флаги]")
    print("  ping 1.1.1.1;8.8.8.8;ya.ru -c 2 -t 1000")
    print("  port 10.15.20.205:1501 --timeout 500")
    print("Команды: help, clear, exit, quit (слово ping можно опустить)\n")

    while True:
        try:
            line = input("autoping> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            return 0

        if not line:
            continue
        cmd = line.lower()
        if cmd in ("exit", "quit"):
            return 0
        if cmd == "clear":
            clear_screen()
            continue
        if cmd in ("help", "-h", "--help"):
            print_help_screen()
            continue

        line_for_split = line.replace(";", " ").replace(",", " ")
        try:
            argv = preprocess_timeout(shlex.split(line_for_split))
        except ValueError as exc:
            print(f"Ошибка разбора ввода: {exc}")
            continue
        if not argv:
            continue

        first = argv[0].lower()
        if first == "ping":
            do_ping(argv[1:])
        elif first == "port":
            do_port(argv[1:])
        elif is_valid_host(argv[0]):
            do_ping(argv)  # неявный ping
        else:
            print(f"{RED}Ошибка:{RESET} неизвестная команда: {argv[0]}")
            print("Доступно: ping, port, help, clear, exit, quit")


def main(argv: Optional[List[str]] = None) -> int:
    setup_encoding()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return interactive_mode()

    first = argv[0].lower()
    if first == "help":
        print_help_screen()
        return 0
    if first == "ping":
        argv = argv[1:]
    elif first == "port":
        return do_port(argv[1:])
    if not argv:
        print_help_screen()
        return 2
    return run_cli(argv)


if __name__ == "__main__":
    try:
        code = main()
    finally:
        if sys.stdout.isatty() and sys.argv[1:]:
            input("\nНажмите Enter для выхода...")
    sys.exit(code)

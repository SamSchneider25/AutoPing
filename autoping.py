#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse
import signal
import shlex
import sys
import time
from typing import List, Optional

from const import __version__, RED, GREEN, YELLOW, CYAN, BOLD, DIM, RESET
from display import (
    setup_encoding, clear_screen, compute_widths, print_table,
    print_summary, export_results, print_help_screen, print_exit_summary,
)
from ping_core import (
    read_hosts_from_file, split_hosts, run_once, PingResult, is_valid_host,
)


class WatchController:
    def __init__(self) -> None:
        self.running = True

    def stop(self, *args) -> None:
        self.running = False


def watch_loop(hosts, args, interval: float, controller: WatchController) -> int:
    from const import HISTORY_WIDTH

    signal.signal(signal.SIGINT, controller.stop)
    history: dict = {}
    try:
        while controller.running:
            clear_screen()
            print(f"AutoPing — режим наблюдения (обновление каждые {interval} с)")
            print(f"{time.strftime('%H:%M:%S')}  Ctrl+C для возврата\n")
            results = run_once(
                hosts, args.count, args.timeout, args.interval, args.size, args.deadline
            )
            for r in results:
                history.setdefault(r.host, []).append(r.status)
                history[r.host] = history[r.host][-HISTORY_WIDTH:]
            widths = compute_widths(results)
            print_table(results, widths, history=history)
            print_summary(results)
            if controller.running:
                print(f"\nСледующее обновление через {interval} с...")
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    clear_screen()
    print_exit_summary(results if results else [])
    return 0


def continuous_loop(hosts, args, controller: WatchController) -> int:
    """Бесконечный пинг с интервалом 1 сек (как ping -t)."""
    signal.signal(signal.SIGINT, controller.stop)
    history: dict = {}
    interval = max(1.0, args.interval or 1.0)
    try:
        while controller.running:
            clear_screen()
            print(f"AutoPing — continuous mode (Ctrl+C для остановки)")
            print(f"{time.strftime('%H:%M:%S')}  Интервал: {interval} с\n")
            results = run_once(
                hosts, 1, args.timeout, args.interval, args.size, args.deadline
            )
            for r in results:
                history.setdefault(r.host, []).append(r.status)
                history[r.host] = history[r.host][-24:]
            widths = compute_widths(results)
            print_table(results, widths, history=history)
            print_summary(results)
            if controller.running:
                print(f"\nСледующий такт через {interval} с...")
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
    clear_screen()
    print_exit_summary(results if results else [])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoping",
        description="Массовый пинг хостов в стиле systemctl status.",
        epilog=(
            "Примеры:\n"
            "  %(prog)s 1.1.1.1;8.8.8.8;ya.ru\n"
            "  %(prog)s -t 1.1.1.1 8.8.8.8           (continuous, без остановки)\n"
            "  %(prog)s --watch 3 -c 2 -t 500 1.1.1.1 8.8.8.8 router\n"
            "  %(prog)s --file hosts.txt --count 10 --size 64\n"
            "  %(prog)s --output json --out results.json 1.1.1.1 google.com\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("hosts", nargs="*", help="Хосты/IP (можно через ; , пробел)")
    parser.add_argument("-f", "--file", type=str, default=None, help="Путь к файлу со списком хостов")
    parser.add_argument("-c", "--count", type=int, default=4, help="Число эхо-запросов на хост")
    parser.add_argument("-t", "--continuous", action="store_true", help="Пинговать без остановки (как ping -t)")
    parser.add_argument("--timeout", type=int, default=1000, help="Таймаут ответа на пакет, мс (по умолчанию 1000)")
    parser.add_argument("-i", "--interval", type=float, default=None, help="Интервал между пакетами, сек (Linux/macOS)")
    parser.add_argument("-s", "--size", type=int, default=None, help="Размер пакета, байт")
    parser.add_argument("-w", "--deadline", type=int, default=None, help="Общее время ожидания, сек (Linux)")
    parser.add_argument("--port", type=str, default=None, help="Порт для справки (ping/ICMP не использует порты)")
    parser.add_argument("--watch", type=float, default=None, help="Перерисовывать экран каждые N секунд")
    parser.add_argument("--output", type=str, choices=["json", "csv"], default=None, help="Экспорт в JSON или CSV")
    parser.add_argument("--out", type=str, default=None, help="Путь к файлу для экспорта")
    parser.add_argument("--no-color", action="store_true", help="Отключить цвета")
    return parser


def collect_hosts(args) -> List[str]:
    """Собирает список хостов из аргументов и файла, поддерживая ; , пробел."""
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


def run_cli(argv: Optional[List[str]] = None) -> int:
    """Основная логика CLI. Возвращает код выхода, не вызывает sys.exit."""
    setup_encoding()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.no_color:
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

    hosts = collect_hosts(args)

    if not hosts:
        print_help_screen()
        return 2

    if args.port:
        print(f"{DIM}Порт {args.port} отмечен для справки (ICMP не использует TCP/UDP порты).{RESET}\n")

    try:
        if args.continuous:
            controller = WatchController()
            return continuous_loop(hosts, args, controller)

        if args.watch:
            controller = WatchController()
            return watch_loop(hosts, args, max(0.5, args.watch), controller)

        results = run_once(hosts, args.count, args.timeout, args.interval, args.size, args.deadline)
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
        try:
            print_exit_summary(results if 'results' in dir() else [])
        except Exception:
            pass
        return 130


def interactive_mode() -> int:
    """Интерактивный REPL: ждет ввод хостов и флагов, выполняет, снова ждет."""
    setup_encoding()
    print(f"{BOLD}AutoPing (PingCtl) v{__version__} — интерактивный режим{RESET}")
    print("Введите хосты и флаги, например: 1.1.1.1;8.8.8.8;ya.ru -c 2 -t 1000")
    print("Команды: help, clear, exit, quit\n")

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

        # Заменяем ; и , на пробелы, чтобы "1.1.1.1;8.8.8.8 -c 2" работал
        line_for_split = line.replace(";", " ").replace(",", " ")
        # Разбиваем ввод на токены (поддерживаем кавычки для строк с ;)
        try:
            argv = shlex.split(line_for_split)
        except ValueError as exc:
            print(f"Ошибка разбора ввода: {exc}")
            continue

        # Предобработка: если в интерактивном режиме за -t идет число, считаем это таймаутом
        # (пользователь привык к -t 1000, а -t без значения — continuous)
        i = 0
        while i < len(argv):
            if argv[i] == "-t" and i + 1 < len(argv) and argv[i + 1].isdigit():
                argv[i] = "--timeout"
                i += 2
                continue
            i += 1

        # Выделяем хосты (все, что не флаги)
        flag_tokens = {"-f", "--file", "-c", "--count", "-t", "--continuous", "--timeout",
                       "-i", "--interval", "-s", "--size", "-w", "--deadline",
                       "--port", "--watch", "--output", "--out", "--no-color",
                       "-V", "--version"}
        host_args: List[str] = []
        flag_args: List[str] = []
        i = 0
        while i < len(argv):
            tok = argv[i]
            if tok in flag_tokens:
                flag_args.append(tok)
                # Флаги с обязательным значением
                if tok in ("-f", "--file", "-c", "--count", "--timeout", "-i", "--interval",
                           "-s", "--size", "-w", "--deadline", "--port", "--watch",
                           "--output", "--out"):
                    if i + 1 < len(argv):
                        flag_args.append(argv[i + 1])
                        i += 2
                    else:
                        i += 1
                else:
                    i += 1
            else:
                host_args.append(tok)
                i += 1

        # Валидация хостов
        invalid = [h for h in host_args if not is_valid_host(h)]
        if invalid:
            print(f"{RED}Ошибка:{RESET} некорректные хосты: {', '.join(invalid)}")
            print("Ожидается IP-адрес или DNS-имя (например: 1.1.1.1, ya.ru)")
            continue

        full_argv = host_args + flag_args
        try:
            code = run_cli(full_argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0
            if code != 0:
                print(f"Ошибка аргументов (код {code})")
            continue

        if code not in (0, 1):
            print(f"(завершено с кодом {code})")
        # После каждого прогона снова ждем ввод


def main(argv: Optional[List[str]] = None) -> int:
    setup_encoding()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        return interactive_mode()
    return run_cli(argv)


if __name__ == "__main__":
    try:
        code = main()
    finally:
        if sys.stdout.isatty() and sys.argv[1:]:
            input("\nНажмите Enter для выхода...")
    sys.exit(code)

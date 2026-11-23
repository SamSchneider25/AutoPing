#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoPing
--------
Массовый пинг хостов в одном терминале с интерфейсом в стиле systemctl status.
Вместо десятков окон `ping 1.1.1.1 -t` — один экран: точка статуса,
статистика пакетов и пояснение ошибки.

Запуск:
    python autoping.py 1.1.1.1 8.8.8.8 router.local
    python autoping.py --watch 3 -c 2 -t 500 1.1.1.1 8.8.8.8
    python autoping.py --count 10 --size 64 ya.ru google.com

Коды выхода:
    0 — все хосты отвечают
    1 — хотя бы один хост не отвечает / деградировано
    2 — ошибка аргументов или внутренняя ошибка
    130 — прервано пользователем (Ctrl+C)
"""

import argparse
import locale
import os
import platform
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Optional

# -------------------- цветовая поддержка --------------------
try:
    import colorama  # type: ignore

    colorama.init(convert=True)
except Exception:  # pragma: no cover
    colorama = None

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def _color_enabled() -> bool:
    if not sys.stdout.isatty():
        return False
    if os.name == "nt" and colorama is None:
        try:
            import ctypes

            mode = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetConsoleMode(
                ctypes.windll.kernel32.GetStdHandle(-11), ctypes.byref(mode)
            )
            return bool(ok)
        except Exception:
            return False
    return True


if not _color_enabled():
    RED = GREEN = YELLOW = CYAN = BOLD = DIM = RESET = ""

# -------------------- данные результата --------------------
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


@dataclass
class PingResult:
    host: str
    status: str = STATUS_UNKNOWN  # active | degraded | failed | unknown
    sent: int = 0
    received: int = 0
    loss_pct: float = 0.0
    min_ms: Optional[float] = None
    avg_ms: Optional[float] = None
    max_ms: Optional[float] = None
    error_code: int = 0
    error_msg: str = ""
    raw: str = ""

    def dot(self) -> str:
        return STATUS_DOT.get(self.status, STATUS_DOT[STATUS_UNKNOWN])

    def status_plain(self) -> str:
        return STATUS_TEXT.get(self.status, STATUS_TEXT[STATUS_UNKNOWN])


# -------------------- утилиты --------------------
def decode_output(data: bytes) -> str:
    """Пытается декодировать вывод ping (Windows может отдавать cp866/cp1251)."""
    preferred = locale.getpreferredencoding(False) or "utf-8"
    encodings = [preferred]
    if os.name == "nt":
        encodings += ["cp866", "cp1252"]
    encodings += ["utf-8", "latin-1"]

    candidates = []
    seen = set()
    for enc in encodings:
        if enc in seen:
            continue
        seen.add(enc)
        try:
            candidates.append(data.decode(enc, errors="replace"))
        except (LookupError, UnicodeDecodeError):
            continue
    if not candidates:
        return ""

    keywords = (
        "отправлено",
        "packets transmitted",
        "статистика",
        "statistics",
        "rtt min",
        "время=",
        "time=",
    )
    low_texts = [c.lower() for c in candidates]
    for text, low in zip(candidates, low_texts):
        if any(k in low for k in keywords):
            return text
    return candidates[0]


def build_ping_command(
    host: str,
    count: int,
    timeout_ms: int,
    interval: Optional[float],
    size: Optional[int],
    deadline: Optional[int],
) -> List[str]:
    sysname = platform.system().lower()
    count = max(1, count)
    timeout_ms = max(1, timeout_ms)

    if sysname == "windows":
        # -n число запросов, -w таймаут на ответ (мс), -l размер буфера
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms)]
        if size:
            cmd += ["-l", str(size)]
        cmd += [host]
        return cmd

    if sysname == "darwin":
        # macOS: -c число, -t таймаут (сек), -i интервал, -s размер
        cmd = ["ping", "-c", str(count), "-t", str(max(1, round(timeout_ms / 1000)))]
        if interval:
            cmd += ["-i", str(interval)]
        if size:
            cmd += ["-s", str(size)]
        cmd += [host]
        return cmd

    # Linux и прочее POSIX: -c, -W таймаут (сек), -w дедлайн, -i интервал, -s размер
    cmd = ["ping", "-c", str(count)]
    if deadline:
        cmd += ["-w", str(deadline)]
    if timeout_ms:
        cmd += ["-W", str(max(1, round(timeout_ms / 1000)))]
    if interval:
        cmd += ["-i", str(interval)]
    if size:
        cmd += ["-s", str(size)]
    cmd += [host]
    return cmd


def _classify(result: PingResult, text: str) -> PingResult:
    low = text.lower()
    sent, received = result.sent, result.received

    # DNS / разрешение имени
    if (
        "не удается найти узел" in low
        or "host not found" in low
        or "name or service not known" in low
        or "nodename nor servname provided" in low
        or "could not find host" in low
        or "ping request could not find host" in low
    ):
        result.error_code = 2
        result.error_msg = "Узел не найден (проверьте DNS/имя)"
        result.status = STATUS_FAILED
        return result

    # системные ошибки
    if (
        "permission denied" in low
        or "недостаточно прав" in low
        or "операция не разрешена" in low
    ):
        result.error_code = 1
        result.error_msg = "Нет прав на ICMP (запустите от администратора / с sudo)"
        result.status = STATUS_FAILED
        return result
    if "сеть недоступна" in low or "network is unreachable" in low:
        result.error_code = 1
        result.error_msg = "Сеть недоступна"
        result.status = STATUS_FAILED
        return result

    if sent == 0:
        result.error_code = 1
        result.error_msg = result.error_msg or "Нет ответов от хоста"
        result.status = STATUS_FAILED
        return result

    if received == sent:
        result.status = STATUS_ACTIVE
    elif received > 0:
        result.status = STATUS_DEGRADED
        result.error_msg = f"Потеря пакетов {result.loss_pct:.1f}%"
    else:
        result.status = STATUS_FAILED
        result.error_msg = result.error_msg or f"Потеря пакетов {result.loss_pct:.1f}%"
        result.error_code = 1
    return result


def parse_windows(text: str, host: str = "") -> PingResult:
    r = PingResult(host=host)
    t = text.replace(",", ".").replace("мc", "мс")

    # пакеты: русский / английский Windows
    m = re.search(
        r"отправлено\s*=\s*(\d+).*получено\s*=\s*(\d+).*потеряно\s*=\s*(\d+)",
        t,
        re.I | re.S,
    )
    if not m:
        m = re.search(
            r"sent\s*=\s*(\d+).*received\s*=\s*(\d+).*lost\s*=\s*(\d+)",
            t,
            re.I | re.S,
        )
    if m:
        r.sent = int(m.group(1))
        r.received = int(m.group(2))
        if r.sent:
            r.loss_pct = round((1 - r.received / r.sent) * 100, 1)

    # min/avg/max: русский / английский
    m = re.search(
        r"минимальн\w*\s*=\s*([\d.,]+)\s*мс.*максимальн\w*\s*=\s*([\d.,]+)\s*мс.*"
        r"среднее\w*\s*=\s*([\d.,]+)\s*мс",
        t,
        re.I | re.S,
    )
    if not m:
        m = re.search(
            r"minimum\s*=\s*([\d.,]+)m?s.*maximum\s*=\s*([\d.,]+)m?s.*"
            r"average\s*=\s*([\d.,]+)m?s",
            t,
            re.I | re.S,
        )
    if m:
        r.min_ms = float(m.group(1).replace(",", "."))
        r.max_ms = float(m.group(2).replace(",", "."))
        r.avg_ms = float(m.group(3).replace(",", "."))
    else:
        times = [float(x) for x in re.findall(r"время\s*=\s*([\d.,]+)\s*мс", t, re.I)]
        if not times:
            times = [float(x) for x in re.findall(r"time\s*=\s*([\d.,]+)m?s", t, re.I)]
        if times:
            r.min_ms, r.max_ms = min(times), max(times)
            r.avg_ms = sum(times) / len(times)
    return r


def parse_posix(text: str, host: str = "") -> PingResult:
    r = PingResult(host=host)

    m = re.search(
        r"(\d+)\s+packets transmitted,\s*(\d+)\s+received"
        r"(?:,\s*(\d+(?:\.\d+)?)%\s+packet loss)?",
        text,
    )
    if m:
        r.sent = int(m.group(1))
        r.received = int(m.group(2))
        if m.group(3):
            r.loss_pct = float(m.group(3))
        elif r.sent:
            r.loss_pct = round((1 - r.received / r.sent) * 100, 1)

    m = re.search(r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/([\d.]+)/([\d.]+)", text)
    if m:
        r.min_ms = float(m.group(1))
        r.avg_ms = float(m.group(2))
        r.max_ms = float(m.group(3))
    else:
        times = [float(x) for x in re.findall(r"time=([\d.]+)\s*ms", text)]
        if times:
            r.min_ms, r.max_ms = min(times), max(times)
            r.avg_ms = sum(times) / len(times)
    return r


def ping_once(
    host: str,
    count: int,
    timeout_ms: int,
    interval: Optional[float],
    size: Optional[int],
    deadline: Optional[int],
) -> PingResult:
    cmd = build_ping_command(host, count, timeout_ms, interval, size, deadline)
    result = PingResult(host=host)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=max(10, (deadline or 0) or count * timeout_ms / 1000 + 10),
        )
        text = decode_output(proc.stdout + proc.stderr)
        result.raw = text
    except FileNotFoundError:
        result.error_code = 2
        result.error_msg = "Команда ping не найдена в системе"
        result.status = STATUS_FAILED
        return result
    except subprocess.TimeoutExpired as exc:
        result.error_code = 1
        result.error_msg = "Превышено время выполнения ping"
        result.raw = decode_output(
            (exc.stdout or b"") + (exc.stderr or b"")
        )
        result.status = STATUS_FAILED
        return result

    sysname = platform.system().lower()
    if sysname == "windows":
        result = parse_windows(result.raw, host=result.host)
    else:
        result = parse_posix(result.raw, host=result.host)
    result = _classify(result, result.raw)
    return result


# -------------------- вывод --------------------
def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def compute_widths(results: List[PingResult]) -> dict:
    w_host = max(18, max((len(r.host) for r in results), default=18))
    w_err = max(22, max((len(r.error_msg) for r in results if r.error_msg), default=22))
    return {
        "host": w_host,
        "status": 16,
        "packets": 9,
        "loss": 8,
        "avg": 9,
        "err": w_err,
    }


def print_header(widths: dict) -> None:
    print(
        f"{BOLD}┌ {'СТАТУС':<2}  {'ХОСТ':<{widths['host']}}  "
        f"{'СТАТУС':<{widths['status']}}  {'ПАКЕТЫ':<{widths['packets']}}  "
        f"{'ПОТЕРЯ':<{widths['loss']}}  {'СРЕДНЕ мс':<{widths['avg']}}  ОШИБКА{RESET}"
    )
    line_len = (
        2 + 2 + widths["host"] + 2 + widths["status"] + 2 + widths["packets"]
        + 2 + widths["loss"] + 2 + widths["avg"] + 2 + widths["err"] + 6
    )
    print(f"{DIM}└{'─' * line_len}{RESET}")


def format_row(r: PingResult, widths: dict) -> str:
    host = r.host[: widths["host"]]
    packets = f"{r.received}/{r.sent}" if r.sent else "—/—"
    loss = f"{r.loss_pct:.1f}%" if r.sent else "— %"
    avg = f"{r.avg_ms:.1f}" if r.avg_ms is not None else "—"
    err = r.error_msg[: widths["err"]]
    return (
        f"{r.dot()}  {host:<{widths['host']}}  "
        f"{STATUS_COLOR.get(r.status, DIM)}{r.status_plain():<{widths['status']}}{RESET}  "
        f"{packets:<{widths['packets']}}  {loss:<{widths['loss']}}  "
        f"{avg:<{widths['avg']}}  {err}"
    )


def print_table(results: List[PingResult], widths: dict) -> None:
    print_header(widths)
    for r in results:
        print(format_row(r, widths))


def print_summary(results: List[PingResult]) -> None:
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


# -------------------- выполнение --------------------
def run_once(
    hosts: List[str], count: int, timeout_ms: int, interval: Optional[float],
    size: Optional[int], deadline: Optional[int],
) -> List[PingResult]:
    results: List[PingResult] = []
    workers = min(len(hosts), 64)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(ping_once, h, count, timeout_ms, interval, size, deadline): h
            for h in hosts
        }
        for fut in as_completed(futs):
            results.append(fut.result())
    # сохранить порядок ввода
    order = {h: i for i, h in enumerate(hosts)}
    results.sort(key=lambda r: order.get(r.host, 0))
    return results


def watch_loop(hosts: List[str], args: argparse.Namespace, interval: float) -> int:
    try:
        while True:
            clear_screen()
            print(f"{BOLD}AutoPing — режим наблюдения (обновление каждые {interval} с){RESET}")
            print(f"{DIM}{time.strftime('%H:%M:%S')}  {RESET}{BOLD}Ctrl+C для остановки{RESET}\n")
            results = run_once(
                hosts, args.count, args.timeout, args.interval, args.size, args.deadline
            )
            widths = compute_widths(results)
            print_table(results, widths)
            print_summary(results)
            print(f"\n{DIM}Следующее обновление через {interval} с...{RESET}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        return 0
    return 0


def _setup_encoding() -> None:
    """Принудительная UTF-8 для вывода (Windows консоль по умолчанию cp866/cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    _setup_encoding()
    parser = argparse.ArgumentParser(
        prog="autoping",
        description="Массовый пинг хостов в стиле systemctl status.",
        epilog="Пример: python autoping.py --watch 3 -c 2 -t 500 1.1.1.1 8.8.8.8 router",
    )
    parser.add_argument("hosts", nargs="+", help="Хосты или IP-адреса для пинга")
    parser.add_argument(
        "-c", "--count", type=int, default=4,
        help="Число эхо-запросов на хост (по умолчанию: 4)",
    )
    parser.add_argument(
        "-i", "--interval", type=float, default=None,
        help="Интервал между пакетами, сек (Linux/macOS)",
    )
    parser.add_argument(
        "-t", "--timeout", type=int, default=1000,
        help="Таймаут ответа на пакет, мс (по умолчанию: 1000)",
    )
    parser.add_argument(
        "-s", "--size", type=int, default=None, help="Размер пакета, байт",
    )
    parser.add_argument(
        "-w", "--deadline", type=int, default=None,
        help="Общее время ожидания, сек (Linux)",
    )
    parser.add_argument(
        "--watch", type=float, default=None,
        help="Перерисовывать экран каждые N секунд (как watch/systemctl)",
    )
    parser.add_argument("--no-color", action="store_true", help="Отключить цвета")
    args = parser.parse_args(argv)

    if args.no_color:
        global RED, GREEN, YELLOW, CYAN, BOLD, DIM, RESET
        RED = GREEN = YELLOW = CYAN = BOLD = DIM = RESET = ""
        STATUS_DOT[STATUS_ACTIVE] = "●"
        STATUS_DOT[STATUS_DEGRADED] = "○"
        STATUS_DOT[STATUS_FAILED] = "✗"
        STATUS_DOT[STATUS_UNKNOWN] = "?"

    if not args.hosts:
        parser.error("Укажите хотя бы один хост")

    try:
        if args.watch:
            return watch_loop(
                args.hosts, args, max(0.5, args.watch)
            )

        results = run_once(
            args.hosts, args.count, args.timeout, args.interval,
            args.size, args.deadline,
        )
        widths = compute_widths(results)
        print(f"{BOLD}AutoPing — результаты проверки{RESET}\n")
        print_table(results, widths)
        print_summary(results)

        failed = sum(
            1 for r in results if r.status in (STATUS_FAILED, STATUS_UNKNOWN)
        )
        degraded = sum(1 for r in results if r.status == STATUS_DEGRADED)
        return 1 if (failed or degraded) else 0
    except KeyboardInterrupt:
        print("\nПрервано пользователем.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

import ipaddress
import locale
import os
import platform
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Optional

from const import (
    STATUS_ACTIVE,
    STATUS_DEGRADED,
    STATUS_FAILED,
    STATUS_UNKNOWN,
    STATUS_DOT,
    STATUS_TEXT,
)


def is_valid_host(token: str) -> bool:
    """Проверяет, что токен похож на IP-адрес или DNS-имя."""
    host, _ = extract_port(token)
    if not host:
        return False
    # localhost валиден всегда
    if host.lower() == "localhost":
        return True
    # IP-адрес (v4/v6)
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    # DNS-like имя:必须 содержать хотя бы одну точку (поддомен.domena)
    if "." not in host:
        return False
    if re.match(
        r"^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)+$",
        host,
    ):
        return True
    return False


@dataclass
class PingResult:
    host: str
    status: str = STATUS_UNKNOWN
    sent: int = 0
    received: int = 0
    loss_pct: float = 0.0
    min_ms: Optional[float] = None
    avg_ms: Optional[float] = None
    max_ms: Optional[float] = None
    error_code: int = 0
    error_msg: str = ""
    raw: str = ""
    packets: List[str] = field(default_factory=list)

    def dot(self) -> str:
        return STATUS_DOT.get(self.status, STATUS_DOT[STATUS_UNKNOWN])

    def status_plain(self) -> str:
        return STATUS_TEXT.get(self.status, STATUS_TEXT[STATUS_UNKNOWN])

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "status": self.status,
            "status_text": self.status_plain(),
            "sent": self.sent,
            "received": self.received,
            "loss_pct": self.loss_pct,
            "min_ms": self.min_ms,
            "avg_ms": self.avg_ms,
            "max_ms": self.max_ms,
            "error_code": self.error_code,
            "error_msg": self.error_msg,
        }


def decode_output(data: bytes) -> str:
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
        "отправлено", "packets transmitted", "статистика", "statistics",
        "rtt min", "время=", "time=",
    )
    low_texts = [c.lower() for c in candidates]
    for text, low in zip(candidates, low_texts):
        if any(k in low for k in keywords):
            return text
    return candidates[0]


def build_ping_command(host, count, timeout_ms, interval, size, deadline, flood=False):
    sysname = platform.system().lower()
    count = max(1, count)
    timeout_ms = max(1, timeout_ms)

    if sysname == "windows":
        # Windows ping.exe не умеет интервал <1с; flood = быстрая серия с live-выводом
        cmd = ["ping", "-n", str(count), "-w", str(timeout_ms)]
        if size:
            cmd += ["-l", str(size)]
        cmd += [host]
        return cmd

    if sysname == "darwin":
        cmd = ["ping", "-c", str(count), "-t", str(max(1, round(timeout_ms / 1000)))]
        if flood and not interval:
            cmd += ["-i", "0.01"]
        elif interval:
            cmd += ["-i", str(interval)]
        if size:
            cmd += ["-s", str(size)]
        cmd += [host]
        return cmd

    cmd = ["ping", "-c", str(count)]
    if deadline:
        cmd += ["-w", str(deadline)]
    if timeout_ms:
        cmd += ["-W", str(max(1, round(timeout_ms / 1000)))]
    if flood and not interval:
        # настоящий flood требует root; иначе минимальный интервал
        try:
            is_root = os.geteuid() == 0
        except AttributeError:
            is_root = False
        cmd += ["-f"] if is_root else ["-i", "0.01"]
    elif interval:
        cmd += ["-i", str(interval)]
    if size:
        cmd += ["-s", str(size)]
    cmd += [host]
    return cmd


def stream_ping(host, count, timeout_ms, interval, size, deadline,
                flood=False, on_packet=None) -> PingResult:
    """Пинг с построчным чтением: on_packet(status) вызывается на каждый пакет (live)."""
    cmd = build_ping_command(host, count, timeout_ms, interval, size, deadline, flood=flood)
    result = PingResult(host=host)
    enc = "cp866" if os.name == "nt" else "utf-8"
    packets: List[str] = []
    chunks: List[bytes] = []
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for raw in proc.stdout:
            chunks.append(raw)
            line = raw.decode(enc, errors="replace")
            status = None
            if re.search(r"(ответ от|reply from|bytes from)", line, re.I) and \
               re.search(r"(время|time=)", line, re.I):
                status = STATUS_ACTIVE
            elif re.search(
                r"(превышен интервал ожидания|request timed out|timed out|"
                r"недоступен|unreachable|no answer yet)", line, re.I
            ):
                status = STATUS_FAILED
            if status:
                packets.append(status)
                if on_packet:
                    on_packet(status)
        proc.wait()
    except FileNotFoundError:
        result.error_code = 2
        result.error_msg = "Команда ping не найдена в системе"
        result.status = STATUS_FAILED
        return result
    except KeyboardInterrupt:
        if proc:
            proc.kill()
        raise

    whole = decode_output(b"".join(chunks))
    result.raw = whole
    parsed = parse_windows(whole, host) if os.name == "nt" else parse_posix(whole, host)
    result.sent = parsed.sent
    result.received = parsed.received
    result.loss_pct = parsed.loss_pct
    result.min_ms = parsed.min_ms
    result.avg_ms = parsed.avg_ms
    result.max_ms = parsed.max_ms
    result.packets = packets or parsed.packets
    return _classify(result, whole)


def _classify(result: PingResult, text: str) -> PingResult:
    low = text.lower()
    sent, received = result.sent, result.received

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
    from const import STATUS_DOT
    r = PingResult(host=host)
    t = text.replace(",", ".").replace("мc", "мс")

    m = re.search(
        r"отправлено\s*=\s*(\d+).*получено\s*=\s*(\d+).*потеряно\s*=\s*(\d+)",
        t, re.I | re.S,
    )
    if not m:
        m = re.search(
            r"sent\s*=\s*(\d+).*received\s*=\s*(\d+).*lost\s*=\s*(\d+)",
            t, re.I | re.S,
        )
    if m:
        r.sent = int(m.group(1))
        r.received = int(m.group(2))
        if r.sent:
            r.loss_pct = round((1 - r.received / r.sent) * 100, 1)

    m = re.search(
        r"минимальн\w*\s*=\s*([\d.,]+)\s*мс.*максимальн\w*\s*=\s*([\d.,]+)\s*мс.*"
        r"среднее\w*\s*=\s*([\d.,]+)\s*мс",
        t, re.I | re.S,
    )
    if not m:
        m = re.search(
            r"minimum\s*=\s*([\d.,]+)m?s.*maximum\s*=\s*([\d.,]+)m?s.*"
            r"average\s*=\s*([\d.,]+)m?s",
            t, re.I | re.S,
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

    # Per-packet: строки ответа/таймаута идут по порядку пакетов
    for m in re.finditer(
        r"(ответ от|reply from|превышен интервал ожидания|request timed out|"
        r"timed out|недоступен|unreachable)",
        t,
        re.I,
    ):
        kind = m.group(1).lower()
        r.packets.append(STATUS_ACTIVE if kind in ("ответ от", "reply from") else STATUS_FAILED)
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

    # Per-packet: по номерам icmp_seq; отсутствующий seq = потерянный пакет
    active_seqs = {int(m.group(1)) for m in re.finditer(r"icmp_seq=(\d+)[^\n]*time=", text)}
    if r.sent:
        r.packets = [
            STATUS_ACTIVE if s in active_seqs else STATUS_FAILED
            for s in range(1, r.sent + 1)
        ]
    return r


def ping_once(host, count, timeout_ms, interval, size, deadline) -> PingResult:
    cmd = build_ping_command(host, count, timeout_ms, interval, size, deadline)
    result = PingResult(host=host)

    try:
        proc_timeout = max(10, (deadline or 0) or count * timeout_ms / 1000 + 10)
        proc = subprocess.run(cmd, capture_output=True, timeout=proc_timeout)
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
        result.raw = decode_output((exc.stdout or b"") + (exc.stderr or b""))
        result.status = STATUS_FAILED
        return result

    sysname = platform.system().lower()
    if sysname == "windows":
        result = parse_windows(result.raw, host=result.host)
    else:
        result = parse_posix(result.raw, host=result.host)
    result = _classify(result, result.raw)
    return result


def split_hosts(raw: str) -> List[str]:
    """Разбивает строку хостов по ; , пробелу. Возвращает список токенов."""
    if not raw:
        return []
    parts = re.split(r"[;\s,]+", raw.strip())
    return [p for p in parts if p]


def extract_port(token: str):
    """Если в токене host:port, возвращает (host, port или None)."""
    if ":" in token:
        h, _, p = token.rpartition(":")
        return h, (p if p else None)
    return token, None


def read_hosts_from_file(path: str) -> List[str]:
    hosts: List[str] = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for token in re.split(r"[\s,;]+", line):
                token = token.strip()
                if token:
                    hosts.append(token)
    return hosts


def run_once(hosts, count, timeout_ms, interval, size, deadline) -> List[PingResult]:
    results: List[PingResult] = []
    workers = min(len(hosts), 64)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(ping_once, h, count, timeout_ms, interval, size, deadline): h
            for h in hosts
        }
        for fut in as_completed(futs):
            results.append(fut.result())
    order = {h: i for i, h in enumerate(hosts)}
    results.sort(key=lambda r: order.get(r.host, 0))
    return results

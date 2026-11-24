import socket
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from const import STATUS_ACTIVE, STATUS_FAILED
from ping_core import is_valid_host


@dataclass
class PortResult:
    host: str
    port: int
    status: str = STATUS_FAILED
    time_ms: Optional[float] = None
    error_msg: str = ""

    def label(self) -> str:
        return f"{self.host}:{self.port}" if self.port else self.host


def parse_port_token(token: str) -> Tuple[Optional[str], Optional[int]]:
    """host:port -> (хост, порт); невалидный токен -> (None, None)."""
    if ":" not in token:
        return None, None
    host, _, port_s = token.rpartition(":")
    if not port_s.isdigit():
        return None, None
    port = int(port_s)
    if not (1 <= port <= 65535) or not is_valid_host(host):
        return None, None
    return host, port


def check_port(host: str, port: int, timeout_ms: int) -> PortResult:
    r = PortResult(host=host, port=port)
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=max(0.1, timeout_ms / 1000)):
            r.time_ms = round((time.monotonic() - start) * 1000, 1)
            r.status = STATUS_ACTIVE
    except (socket.timeout, TimeoutError):
        r.error_msg = "Таймаут соединения"
    except ConnectionRefusedError:
        r.error_msg = "Порт закрыт (refused)"
    except socket.gaierror:
        r.error_msg = "Узел не найден (DNS)"
    except OSError as exc:
        r.error_msg = f"Ошибка сети: {exc.strerror or exc}"
    return r


def run_ports(tokens: List[str], timeout_ms: int) -> List[PortResult]:
    results: List[PortResult] = []
    for token in tokens:
        host, port = parse_port_token(token)
        if host is None or port is None:
            results.append(PortResult(host=token, port=0, error_msg="Ожидается host:port"))
            continue
        results.append(check_port(host, port, timeout_ms))
    return results

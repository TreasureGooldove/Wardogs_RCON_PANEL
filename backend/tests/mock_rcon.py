"""Local read-only Wardogs HTTP RCON fixture; never connects to a real server."""

from __future__ import annotations

import argparse
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator

MOCK_BEARER = "local-mock-only"
READ_PATHS = (
    "/v1/capabilities", "/v1/status", "/v1/players", "/v1/rotation",
    "/v1/catalog/maps", "/v1/catalog/experiences", "/v1/catalog/lightings",
)
NORMAL: dict[str, object] = {
    "/v1/capabilities": {"routes": [f"GET {path}" for path in READ_PATHS]},
    "/v1/status": {
        "map": "Narva", "experiences": ["Invasion"], "lighting": "Day",
        "players": {"current": 2, "max": 100},
        "factionScores": [{"name": "Team A", "score": 120}, {"name": "Team B", "score": 105}],
    },
    "/v1/players": {"players": [
        {"steamId": "76561198000000001", "name": "同名玩家", "faction": "A", "kills": 3},
        {"steamId": "76561198000000002", "name": "同名玩家", "faction": "B"},
    ]},
    "/v1/rotation": {"mode": "ordered", "entries": [
        {"map": "Narva", "experiences": ["Invasion"], "lighting": "Day"},
        {"map": "Fallujah", "experiences": [], "lighting": "Night"},
    ]},
    "/v1/catalog/maps": {"maps": [{"id": "Narva", "name": "Narva"}]},
    "/v1/catalog/experiences": {"experiences": ["Invasion", "AAS"]},
    "/v1/catalog/lightings": {"lightings": ["Day", "Night"]},
}
EMPTY: dict[str, object] = {
    **NORMAL, "/v1/players": {"players": []},
    "/v1/rotation": {"mode": "ordered", "entries": []},
    "/v1/catalog/maps": {"maps": []},
    "/v1/catalog/experiences": {"experiences": []},
    "/v1/catalog/lightings": {"lightings": []},
}


class MockRconServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], scenario: str = "normal") -> None:
        super().__init__(address, MockRconHandler)
        self.scenario = scenario
        self.requests: list[tuple[str, str]] = []
        self.write_attempted = False
        self._lock = threading.Lock()

    def record(self, method: str, path: str) -> None:
        with self._lock:
            self.requests.append((method, path))
            if method != "GET":
                self.write_attempted = True


class MockRconHandler(BaseHTTPRequestHandler):
    server: MockRconServer

    def log_message(self, _format: str, *_args: object) -> None:
        return  # Avoid leaking request headers and player identifiers.

    def _send(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - standard-library handler API
        path = self.path.split("?", 1)[0]
        self.server.record("GET", self.path)
        if self.path != path or path not in READ_PATHS:
            self._send(404, {"error": "route not found"})
        elif self.headers.get("Authorization") != f"Bearer {MOCK_BEARER}":
            self._send(401, {"error": "unauthorized"})
        elif self.server.scenario == "error" or (
            self.server.scenario == "unknown" and path == "/v1/capabilities"
        ):
            self._send(503, {"error": "simulated failure"})
        else:
            self._send(200, (EMPTY if self.server.scenario == "empty" else NORMAL)[path])

    def _reject_non_get(self) -> None:
        self.server.record(self.command, self.path)
        self._send(405, {"error": "read-only mock"})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _reject_non_get


@contextmanager
def running_mock_rcon(
    scenario: str = "normal", host: str = "127.0.0.1", port: int = 0
) -> Iterator[MockRconServer]:
    server = MockRconServer((host, port), scenario=scenario)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local read-only Wardogs RCON fixture")
    parser.add_argument("--host", choices=("127.0.0.1", "localhost", "::1"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--scenario", choices=("normal", "empty", "unknown", "error"), default="normal")
    args = parser.parse_args()
    with MockRconServer((args.host, args.port), scenario=args.scenario) as server:
        print(f"Local mock RCON on http://{args.host}:{server.server_port} ({args.scenario})")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

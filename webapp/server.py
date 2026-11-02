"""Local web UI for the Room Humidity Simulator (standard library only).

Run from the project root:

    .venv/bin/python -m webapp.server            # http://127.0.0.1:8765
    .venv/bin/python -m webapp.server --port 9000

Endpoints:

- ``GET  /``               the single-page app (``webapp/static``)
- ``GET  /api/defaults``   default scenario and sweep parameters
- ``GET  /api/controllers`` control algorithms (built-in + ``controllers/*.py``)
- ``POST /api/simulate``   scenario JSON -> time series + summary
- ``POST /api/sweep``      {scenario, parameter, from, to, points} -> summaries
"""

from __future__ import annotations

import argparse
import errno
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from webapp.control_runner import list_controllers
from webapp.scenario import DEFAULT_SCENARIO, SWEEP_PARAMETERS, run_scenario, run_sweep

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY_BYTES = 1_000_000


def static_target(path: str) -> Optional[Path]:
    """File under ``STATIC_DIR`` for a URL path, or ``None`` (also for traversal)."""
    if path == "/":
        path = "/index.html"
    target = (STATIC_DIR / unquote(path).lstrip("/")).resolve()
    if STATIC_DIR not in target.parents or not target.is_file():
        return None
    return target


class Handler(BaseHTTPRequestHandler):
    server_version = "HumiditySim/1.0"

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        print("[web] " + fmt % args)

    def _send_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        path = self.path.split("?", 1)[0]
        if path == "/api/controllers":
            self._send_json({"controllers": list_controllers()})
            return
        if path == "/api/defaults":
            self._send_json(
                {
                    "scenario": DEFAULT_SCENARIO,
                    "sweep_parameters": {k: {"label": l, "unit": u} for k, (l, u) in SWEEP_PARAMETERS.items()},
                }
            )
            return
        target = static_target(path)
        if target is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json({"error": {"field": "body", "message": "missing or too large"}}, HTTPStatus.BAD_REQUEST)
            return
        try:
            request = json.loads(self.rfile.read(length))
        except ValueError:
            self._send_json({"error": {"field": "body", "message": "invalid JSON"}}, HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/simulate":
            result = run_scenario(request)
        elif self.path == "/api/sweep":
            result = run_sweep(request)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        status = HTTPStatus.BAD_REQUEST if "error" in result else HTTPStatus.OK
        self._send_json(_finite(result), status)


def _finite(value):
    """Replace non-finite floats (e.g. unlimited power) with None for strict JSON."""
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise SystemExit(
                "Port {port} is already in use: the UI is probably already running at "
                "http://{host}:{port} (stop it with Ctrl+C in its terminal), "
                "or start on another port with --port {other}.".format(
                    host=args.host, port=args.port, other=args.port + 1
                )
            )
        raise
    print("Room Humidity Simulator UI on http://{}:{}".format(args.host, args.port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Shared validation, publishing, and HTTP support for PKU scrape receivers."""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import stat
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_FILE_MODE = 0o644
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_PROGRESS_BYTES = 64 * 1024
PKU_ORIGIN = "https://elective.pku.edu.cn"
TOKEN_HEADER = "X-PKU-Receiver-Token"
ALLOWED_REQUEST_HEADERS = frozenset(("content-type", TOKEN_HEADER.lower()))
PATH_METHODS = {
    "/inpage.js": "GET",
    "/progress": "POST",
    "/done": "POST",
}
VALIDATION_KEYS = (
    "missingCourseCodes",
    "missingDetailLinks",
    "suspiciousPages",
)


@dataclass(frozen=True)
class ReceiverConfig:
    script: Path
    raw_output: Path
    final_output: Path
    term: str
    label: str


class PayloadRejected(ValueError):
    """Raised when a received scrape payload is not safe to publish."""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"nonstandard JSON constant: {value}")


def _decode_json(body: bytes) -> object:
    return json.loads(
        body.decode("utf-8"),
        parse_constant=_reject_json_constant,
    )


def validate_payload(payload: object, config: ReceiverConfig) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise PayloadRejected("payload must be an object")
    if payload.get("term") != config.term:
        raise PayloadRejected("payload term does not match receiver term")

    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise PayloadRejected("rows must be a nonempty list")

    if "errors" not in payload or not isinstance(payload["errors"], list):
        raise PayloadRejected("errors must be an explicitly present list")
    if payload["errors"]:
        raise PayloadRejected("payload reports scrape errors")

    validation = payload.get("validation")
    if not isinstance(validation, Mapping):
        raise PayloadRejected("validation must be an object")
    for key in VALIDATION_KEYS:
        if key not in validation or not isinstance(validation[key], list):
            raise PayloadRejected(f"validation.{key} must be an explicitly present list")
        if validation[key]:
            raise PayloadRejected(f"validation failed: {key}")

    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise PayloadRejected(f"row {index} must be an object")
        if row.get("数据学期") != config.term:
            raise PayloadRejected(f"row {index} term does not match receiver term")
        basic = row.get("基本信息")
        if not isinstance(basic, Mapping):
            raise PayloadRejected(f"row {index} 基本信息 must be an object")
        course_code = basic.get("课程号")
        if not isinstance(course_code, str) or not course_code.strip():
            raise PayloadRejected(f"row {index} 课程号 must be a nonblank string")
        detail = row.get("详细信息")
        if detail is not None and not isinstance(detail, Mapping):
            raise PayloadRejected(f"row {index} 详细信息 must be an object or null")

    return rows


def _directory_fsync(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, body: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (
        stat.S_IMODE(path.stat().st_mode)
        if path.exists()
        else DEFAULT_FILE_MODE
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        _directory_fsync(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def publish_payload(body: bytes, config: ReceiverConfig) -> list[dict[str, Any]]:
    _atomic_write(config.raw_output, body)
    try:
        payload = _decode_json(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise PayloadRejected("body must be valid UTF-8 JSON") from exc

    rows = validate_payload(payload, config)
    final_body = (
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    _atomic_write(config.final_output, final_body)
    return rows


def make_handler(
    config: ReceiverConfig,
    token: str,
) -> type[BaseHTTPRequestHandler]:
    token_bytes = token.encode("utf-8")

    class ReceiverHandler(BaseHTTPRequestHandler):
        server_version = "PkuReceiver/2.0"
        sys_version = ""

        def log_message(self, format_string: str, *args: object) -> None:
            print(
                f"[receiver] {self.client_address[0]} - {format_string % args}",
                flush=True,
            )

        def _respond(
            self,
            status: int,
            body: bytes = b"",
            content_type: str = "text/plain; charset=utf-8",
        ) -> None:
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", PKU_ORIGIN)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header(
                "Access-Control-Allow-Headers",
                f"Content-Type, {TOKEN_HEADER}",
            )
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            if body:
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _authorized(self) -> bool:
            if self.headers.get("Origin") != PKU_ORIGIN:
                self._respond(403, b"forbidden origin\n")
                return False
            supplied_token = self.headers.get(TOKEN_HEADER, "")
            if not hmac.compare_digest(supplied_token.encode("utf-8"), token_bytes):
                self._respond(403, b"authentication failed\n")
                return False
            return True

        def _read_body(self, limit: int) -> bytes | None:
            if self.headers.get_all("Transfer-Encoding"):
                self._respond(400, b"unsupported transfer framing\n")
                return None
            lengths = self.headers.get_all("Content-Length")
            if not lengths:
                self._respond(411, b"content length required\n")
                return None
            if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
                self._respond(400, b"invalid content length\n")
                return None
            normalized_length = lengths[0].lstrip("0") or "0"
            limit_text = str(limit)
            if (
                len(normalized_length) > len(limit_text)
                or (
                    len(normalized_length) == len(limit_text)
                    and normalized_length > limit_text
                )
            ):
                self._respond(413, b"request body too large\n")
                return None
            length = int(normalized_length)
            body = self.rfile.read(length)
            if len(body) != length:
                self._respond(400, b"truncated request body\n")
                return None
            return body

        def do_OPTIONS(self) -> None:
            path = urlparse(self.path).path
            if self.headers.get("Origin") != PKU_ORIGIN:
                self._respond(403, b"forbidden origin\n")
                return
            expected_method = PATH_METHODS.get(path)
            if expected_method is None:
                self._respond(404, b"unknown path\n")
                return
            requested_method = self.headers.get("Access-Control-Request-Method")
            if requested_method is None:
                self._respond(400, b"requested method required\n")
                return
            if requested_method.upper() != expected_method:
                self._respond(403, b"requested method not allowed\n")
                return
            requested_headers = {
                item.strip().lower()
                for item in self.headers.get(
                    "Access-Control-Request-Headers",
                    "",
                ).split(",")
                if item.strip()
            }
            if not requested_headers.issubset(ALLOWED_REQUEST_HEADERS):
                self._respond(403, b"requested header not allowed\n")
                return
            self._respond(204)

        def do_GET(self) -> None:
            if not self._authorized():
                return
            path = urlparse(self.path).path
            if path != "/inpage.js":
                self._respond(404, b"unknown path\n")
                return
            receiver_url = f"http://127.0.0.1:{self.server.server_port}"
            try:
                code = config.script.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                self._respond(500, b"unable to serve scraper\n")
                return
            data = (
                code.replace("__RECEIVER_URL__", receiver_url)
                .replace("__RECEIVER_TOKEN__", token)
                .encode("utf-8")
            )
            self._respond(
                200,
                data,
                content_type="application/javascript; charset=utf-8",
            )

        def do_POST(self) -> None:
            if not self._authorized():
                return
            path = urlparse(self.path).path
            if path not in ("/progress", "/done"):
                self._respond(404, b"unknown path\n")
                return
            limit = MAX_PROGRESS_BYTES if path == "/progress" else MAX_BODY_BYTES
            body = self._read_body(limit)
            if body is None:
                return
            if path == "/progress":
                self._handle_progress(body)
            else:
                self._handle_done(body)

        def _handle_progress(self, body: bytes) -> None:
            try:
                progress = _decode_json(body)
            except (UnicodeDecodeError, ValueError):
                self._respond(400, b"progress must be valid UTF-8 JSON\n")
                return
            if not isinstance(progress, Mapping):
                self._respond(400, b"progress must be a JSON object\n")
                return
            print(
                f"[progress] {json.dumps(progress, ensure_ascii=False)}",
                flush=True,
            )
            self._respond(200, b"ok\n")

        def _handle_done(self, body: bytes) -> None:
            try:
                rows = publish_payload(body, config)
            except PayloadRejected as exc:
                self._respond(422, f"payload rejected: {exc}\n".encode("utf-8"))
                return
            except OSError:
                self._respond(500, b"payload publish failed\n")
                return
            except Exception:
                self._respond(500, b"payload processing failed\n")
                return

            print(f"[done] raw payload: {config.raw_output}", flush=True)
            print(f"[done] final json : {config.final_output}", flush=True)
            print(f"[done] rows       : {len(rows)}", flush=True)
            self._respond(200, b"ok\n")
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    return ReceiverHandler


def _loader_command(receiver_url: str, token: str) -> str:
    url_json = json.dumps(f"{receiver_url}/inpage.js")
    token_json = json.dumps(token)
    return (
        f"fetch({url_json}, {{headers: {{{json.dumps(TOKEN_HEADER)}: {token_json}}}}})"
        ".then(r => { if (!r.ok) throw new Error(`receiver HTTP ${r.status}`); "
        "return r.text(); })"
        ".then(code => { const script = document.createElement('script'); "
        "script.textContent = code; document.documentElement.appendChild(script); "
        "script.remove(); });"
    )


def run_receiver(config: ReceiverConfig, port: int = 8765) -> None:
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(
        ("127.0.0.1", port),
        make_handler(config, token),
    )
    receiver_url = f"http://127.0.0.1:{server.server_port}"
    print(f"[receiver] {config.label}", flush=True)
    print("[receiver] Paste this command in the current PKU course page console:", flush=True)
    print(_loader_command(receiver_url, token), flush=True)
    print("[receiver] waiting for authenticated /done ...", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    print("[receiver] stopped", flush=True)

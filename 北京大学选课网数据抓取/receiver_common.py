#!/usr/bin/env python3
"""Shared validation, publishing, and HTTP support for PKU scrape receivers."""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import socket
import stat
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


DEFAULT_FILE_MODE = 0o644
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_PROGRESS_BYTES = 64 * 1024
BODY_READ_TIMEOUT_SECONDS = 30.0
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
UNDERGRAD_COURSE_TYPES = (
    "培养方案",
    "专业课",
    "政治课",
    "英语课",
    "体育课",
    "通识课",
    "公选课",
    "计算机基础课",
    "劳动教育课",
    "思政选择性必修课",
)
UNDERGRAD_BASIC_FIELDS = (
    "课程号",
    "课程名",
    "课程类别",
    "学分",
    "教师",
    "班号",
    "开课单位",
    "专业",
    "年级",
    "上课时间及教室",
    "限数已选",
    "自选PNP",
    "备注",
)
UNDERGRAD_DETAIL_FIELDS = (
    "英文名称",
    "先修课程",
    "中文简介",
    "英文简介",
    "成绩记载方式",
    "通识课所属系列",
    "授课语言",
    "教材",
    "参考书",
    "教学大纲",
    "教学评估",
)
GRADUATE_BASIC_FIELDS = tuple(
    field for field in UNDERGRAD_BASIC_FIELDS if field != "自选PNP"
)
GRADUATE_DETAIL_FIELDS = (
    "英文名称",
    "周学时",
    "总学时",
    "开课学期",
    "修读对象",
    "参考书",
    "课程简介",
    "详情备注",
    "大纲",
)
DETAIL_PATH = (
    "/elective2008/edu/pku/stu/elective/controller/courseQuery/goNested.do"
)


@dataclass(frozen=True)
class ReceiverConfig:
    script: Path
    raw_output: Path
    final_output: Path
    term: str
    label: str
    level: str
    course_types: tuple[str, ...]
    stats_bucket: str | None
    basic_fields: tuple[str, ...]
    optional_basic_fields: tuple[str, ...]
    detail_fields: tuple[str, ...]
    unique_key_fields: tuple[str, ...]


class PayloadRejected(ValueError):
    """Raised when a received scrape payload is not safe to publish."""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"nonstandard JSON constant: {value}")


def _decode_json(body: bytes) -> object:
    return json.loads(
        body.decode("utf-8"),
        parse_constant=_reject_json_constant,
    )


def _validate_detail_link(link: object, sequence: str, row_index: int) -> None:
    if not isinstance(link, str) or not link.strip():
        raise PayloadRejected(f"row {row_index} 详情链接 must be a nonblank string")
    parsed = urlparse(link)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "elective.pku.edu.cn"
        or parsed.path != DETAIL_PATH
        or parsed.fragment
    ):
        raise PayloadRejected(f"row {row_index} 详情链接 is not a PKU detail URL")
    try:
        query = parse_qs(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError as exc:
        raise PayloadRejected(f"row {row_index} 详情链接 query is invalid") from exc
    if query != {"course_seq_no": [sequence]}:
        raise PayloadRejected(f"row {row_index} 详情链接 course_seq_no mismatch")


def _row_unique_key(
    row: Mapping[str, Any],
    basic: Mapping[str, Any],
    config: ReceiverConfig,
) -> tuple[str, ...]:
    return tuple(
        row[field] if field == "课程类型" else basic[field]
        for field in config.unique_key_fields
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

    total_rows = validation.get("totalRows")
    if isinstance(total_rows, bool) or not isinstance(total_rows, int):
        raise PayloadRejected("validation.totalRows must be an integer")
    duplicate_sequences = validation.get("duplicateSeqs")
    if not isinstance(duplicate_sequences, list):
        raise PayloadRejected("validation.duplicateSeqs must be a list")
    duplicate_keys = validation.get("duplicateKeys")
    if not isinstance(duplicate_keys, list):
        raise PayloadRejected("validation.duplicateKeys must be a list")
    if duplicate_keys:
        raise PayloadRejected("validation failed: duplicateKeys")

    actual_type_counts: Counter[str] = Counter()
    seen_sequences: set[str] = set()
    actual_duplicate_sequences: list[str] = []
    seen_unique_keys: set[tuple[str, ...]] = set()

    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise PayloadRejected(f"row {index} must be an object")
        if row.get("数据学期") != config.term:
            raise PayloadRejected(f"row {index} term does not match receiver term")
        basic = row.get("基本信息")
        if not isinstance(basic, Mapping):
            raise PayloadRejected(f"row {index} 基本信息 must be an object")
        basic_keys = set(basic)
        required_basic_keys = set(config.basic_fields)
        allowed_basic_keys = required_basic_keys | set(config.optional_basic_fields)
        if not required_basic_keys.issubset(basic_keys):
            raise PayloadRejected(f"row {index} 基本信息 is missing required fields")
        if not basic_keys.issubset(allowed_basic_keys):
            raise PayloadRejected(f"row {index} 基本信息 has unexpected fields")
        if any(not isinstance(value, str) for value in basic.values()):
            raise PayloadRejected(f"row {index} 基本信息 values must be strings")
        course_code = basic.get("课程号")
        if not isinstance(course_code, str) or not course_code.strip():
            raise PayloadRejected(f"row {index} 课程号 must be a nonblank string")
        detail = row.get("详细信息")
        if not isinstance(detail, Mapping):
            raise PayloadRejected(f"row {index} 详细信息 must be an object")
        if set(detail) != set(config.detail_fields):
            raise PayloadRejected(f"row {index} 详细信息 fields do not match schema")
        if any(not isinstance(value, str) for value in detail.values()):
            raise PayloadRejected(f"row {index} 详细信息 values must be strings")

        sequence = row.get("课程序号")
        if not isinstance(sequence, str) or not sequence.strip():
            raise PayloadRejected(f"row {index} 课程序号 must be a nonblank string")
        _validate_detail_link(row.get("详情链接"), sequence, index)

        if config.level == "undergraduate":
            course_type = row.get("课程类型")
            if course_type not in config.course_types:
                raise PayloadRejected(f"row {index} 课程类型 is not configured")
            actual_type_counts[course_type] += 1

        if sequence in seen_sequences:
            actual_duplicate_sequences.append(sequence)
        seen_sequences.add(sequence)

        unique_key = _row_unique_key(row, basic, config)
        if unique_key in seen_unique_keys:
            raise PayloadRejected(f"row {index} duplicates the configured unique key")
        seen_unique_keys.add(unique_key)

    if total_rows != len(rows):
        raise PayloadRejected("validation.totalRows does not match rows")
    if duplicate_sequences != actual_duplicate_sequences:
        raise PayloadRejected("validation.duplicateSeqs does not match rows")

    stats = payload.get("stats")
    if not isinstance(stats, Mapping):
        raise PayloadRejected("stats must be an object")
    expected_stats_keys = (
        set(config.course_types)
        if config.level == "undergraduate"
        else {config.stats_bucket}
    )
    if set(stats) != expected_stats_keys:
        raise PayloadRejected("stats keys do not match receiver configuration")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in stats.values()
    ):
        raise PayloadRejected("stats counts must be nonnegative integers")
    if config.level == "undergraduate":
        if any(stats[key] != actual_type_counts[key] for key in config.course_types):
            raise PayloadRejected("stats counts do not match row course types")
    elif stats[config.stats_bucket] != len(rows):
        raise PayloadRejected("graduate stats count does not match rows")
    if sum(stats.values()) != len(rows):
        raise PayloadRejected("stats total does not match rows")

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
    had_original = path.exists()
    mode = stat.S_IMODE(path.stat().st_mode) if had_original else DEFAULT_FILE_MODE
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    backup_path: Path | None = None
    restore_path: Path | None = None
    preserve_backup = False
    committed = False
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())

        if had_original:
            backup_descriptor, backup_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".backup",
                dir=path.parent,
            )
            os.close(backup_descriptor)
            backup_path = Path(backup_name)
            backup_path.unlink()
            os.link(path, backup_path, follow_symlinks=False)

        os.replace(temporary_path, path)
        try:
            _directory_fsync(path.parent)
        except BaseException:
            if backup_path is not None:
                try:
                    restore_descriptor, restore_name = tempfile.mkstemp(
                        prefix=f".{path.name}.",
                        suffix=".restore",
                        dir=path.parent,
                    )
                    os.close(restore_descriptor)
                    restore_path = Path(restore_name)
                    restore_path.unlink()
                    os.link(
                        backup_path,
                        restore_path,
                        follow_symlinks=False,
                    )
                    os.replace(restore_path, path)
                except BaseException:
                    preserve_backup = True
                    raise
            else:
                path.unlink(missing_ok=True)
            try:
                _directory_fsync(path.parent)
            except BaseException:
                preserve_backup = backup_path is not None
                raise
            if backup_path is not None:
                try:
                    backup_path.unlink()
                except OSError:
                    pass
                else:
                    backup_path = None
                    try:
                        _directory_fsync(path.parent)
                    except OSError:
                        pass
            raise
        committed = True

        if backup_path is not None:
            try:
                backup_path.unlink()
            except OSError:
                pass
            else:
                backup_path = None
                try:
                    _directory_fsync(path.parent)
                except OSError:
                    pass
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if committed:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            temporary_path.unlink(missing_ok=True)
        if restore_path is not None:
            try:
                restore_path.unlink(missing_ok=True)
            except OSError:
                pass
        if backup_path is not None and not preserve_backup:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
            else:
                try:
                    _directory_fsync(path.parent)
                except OSError:
                    pass


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
    done_lock = threading.Lock()
    done_committed = False

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
            allow_private_network: bool = False,
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
            if allow_private_network:
                self.send_header("Access-Control-Allow-Private-Network", "true")
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
            deadline = time.monotonic() + BODY_READ_TIMEOUT_SECONDS
            original_timeout = self.connection.gettimeout()
            body = bytearray()
            failure: tuple[int, bytes] | None = None
            try:
                while len(body) < length:
                    remaining_seconds = deadline - time.monotonic()
                    if remaining_seconds <= 0:
                        raise TimeoutError
                    self.connection.settimeout(remaining_seconds)
                    chunk = self.rfile.read1(min(length - len(body), 64 * 1024))
                    if not chunk:
                        failure = (400, b"truncated request body\n")
                        break
                    body.extend(chunk)
            except (socket.timeout, TimeoutError):
                failure = (408, b"request body read timed out\n")
            except (ConnectionResetError, BrokenPipeError, OSError):
                failure = (400, b"request body read failed\n")
            finally:
                try:
                    self.connection.settimeout(original_timeout)
                except OSError:
                    pass

            if failure is not None:
                self.close_connection = True
                self._respond(*failure)
                return None
            return bytes(body)

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
            private_network = self.headers.get(
                "Access-Control-Request-Private-Network"
            )
            if private_network not in (None, "true"):
                self._respond(403, b"private network request not allowed\n")
                return
            self._respond(
                204,
                allow_private_network=private_network == "true",
            )

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
            nonlocal done_committed
            with done_lock:
                if done_committed:
                    self._respond(409, b"payload already published\n")
                    return
                try:
                    rows = publish_payload(body, config)
                except PayloadRejected as exc:
                    self._respond(
                        422,
                        f"payload rejected: {exc}\n".encode("utf-8"),
                    )
                    return
                except OSError:
                    self._respond(500, b"payload publish failed\n")
                    return
                except Exception:
                    self._respond(500, b"payload processing failed\n")
                    return

                done_committed = True
                threading.Thread(
                    target=self.server.shutdown,
                    daemon=True,
                ).start()

            try:
                print(f"[done] raw payload: {config.raw_output}", flush=True)
                print(f"[done] final json : {config.final_output}", flush=True)
                print(f"[done] rows       : {len(rows)}", flush=True)
                self._respond(200, b"ok\n")
            except Exception:
                self.close_connection = True

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

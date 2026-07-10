from __future__ import annotations

import contextlib
import http.client
import importlib
import io
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections import Counter, UserDict
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from 北京大学选课网数据抓取 import receiver_common
from 北京大学选课网数据抓取.receiver_common import (
    MAX_BODY_BYTES,
    MAX_PROGRESS_BYTES,
    PKU_ORIGIN,
    TOKEN_HEADER,
    PayloadRejected,
    ReceiverConfig,
    make_handler,
    publish_payload,
    run_receiver,
    validate_payload,
)


TERM = "26-27学年第1学期"
COURSE_TYPES = ("专业课", "公选课")
ALL_UNDERGRAD_COURSE_TYPES = (
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


class ReceiverTestCase(unittest.TestCase):
    def config(self, root: str | Path) -> ReceiverConfig:
        root = Path(root)
        return ReceiverConfig(
            script=root / "script.js",
            raw_output=root / "archive" / "raw.json",
            final_output=root / "published" / "final.json",
            term=TERM,
            label="test receiver",
            level="undergraduate",
            course_types=COURSE_TYPES,
            stats_bucket=None,
            basic_fields=UNDERGRAD_BASIC_FIELDS,
            optional_basic_fields=("英语等级",),
            detail_fields=UNDERGRAD_DETAIL_FIELDS,
            unique_key_fields=("课程类型", "课程号", "班号", "教师"),
        )

    def graduate_config(self, root: str | Path) -> ReceiverConfig:
        root = Path(root)
        return ReceiverConfig(
            script=root / "graduate-script.js",
            raw_output=root / "archive" / "graduate-raw.json",
            final_output=root / "published" / "graduate-final.json",
            term=TERM,
            label="graduate test receiver",
            level="graduate",
            course_types=(),
            stats_bucket="研究生课",
            basic_fields=GRADUATE_BASIC_FIELDS,
            optional_basic_fields=(),
            detail_fields=GRADUATE_DETAIL_FIELDS,
            unique_key_fields=("课程号", "班号", "教师", "开课单位"),
        )

    def undergrad_row(
        self,
        *,
        course_type: str = "专业课",
        course_code: str = "001",
        class_no: str = "1",
        teacher: str = "测试教师",
        sequence: str = "SEQ001",
    ) -> dict:
        basic = {field: "" for field in UNDERGRAD_BASIC_FIELDS}
        basic.update(
            {
                "课程号": course_code,
                "课程名": "测试课程",
                "教师": teacher,
                "班号": class_no,
            }
        )
        return {
            "课程类型": course_type,
            "数据学期": TERM,
            "详情链接": (
                "https://elective.pku.edu.cn/elective2008/edu/pku/stu/"
                "elective/controller/courseQuery/goNested.do?course_seq_no="
                f"{sequence}"
            ),
            "课程序号": sequence,
            "基本信息": basic,
            "详细信息": {field: "" for field in UNDERGRAD_DETAIL_FIELDS},
        }

    def graduate_row(self, *, sequence: str = "GR001") -> dict:
        basic = {field: "" for field in GRADUATE_BASIC_FIELDS}
        basic.update(
            {
                "课程号": "G001",
                "课程名": "研究生测试课程",
                "教师": "测试教师",
                "班号": "00",
                "开课单位": "测试学院",
            }
        )
        return {
            "数据学期": TERM,
            "详情链接": (
                "https://elective.pku.edu.cn/elective2008/edu/pku/stu/"
                "elective/controller/courseQuery/goNested.do?course_seq_no="
                f"{sequence}"
            ),
            "课程序号": sequence,
            "基本信息": basic,
            "详细信息": {field: "" for field in GRADUATE_DETAIL_FIELDS},
        }

    def valid_payload(self, rows: list[dict] | None = None) -> dict:
        rows = rows if rows is not None else [self.undergrad_row()]
        counts = Counter(row["课程类型"] for row in rows)
        seen_sequences = set()
        duplicate_sequences = []
        for row in rows:
            sequence = row["课程序号"]
            if sequence in seen_sequences:
                duplicate_sequences.append(sequence)
            seen_sequences.add(sequence)
        return {
            "term": TERM,
            "stats": {course_type: counts[course_type] for course_type in COURSE_TYPES},
            "rows": rows,
            "errors": [],
            "validation": {
                "totalRows": len(rows),
                "duplicateSeqs": duplicate_sequences,
                "duplicateKeys": [],
                "missingCourseCodes": [],
                "missingDetailLinks": [],
                "suspiciousPages": [],
            },
        }

    def valid_graduate_payload(self, rows: list[dict] | None = None) -> dict:
        rows = rows if rows is not None else [self.graduate_row()]
        return {
            "term": TERM,
            "stats": {"研究生课": len(rows)},
            "rows": rows,
            "errors": [],
            "validation": {
                "totalRows": len(rows),
                "duplicateSeqs": [],
                "duplicateKeys": [],
                "missingCourseCodes": [],
                "missingDetailLinks": [],
                "suspiciousPages": [],
            },
        }


class ReceiverValidationTests(ReceiverTestCase):
    def assert_rejected(self, payload: object, config: ReceiverConfig) -> None:
        with self.assertRaises(PayloadRejected):
            validate_payload(payload, config)

    def test_config_is_frozen(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with self.assertRaises((AttributeError, TypeError)):
                config.term = "wrong"  # type: ignore[misc]

    def test_accepts_complete_undergraduate_and_graduate_mapping_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            payload = self.valid_payload()
            self.assertIs(validate_payload(UserDict(payload), config), payload["rows"])

            graduate_config = self.graduate_config(tmp)
            graduate_payload = self.valid_graduate_payload()
            self.assertIs(
                validate_payload(UserDict(graduate_payload), graduate_config),
                graduate_payload["rows"],
            )

    def test_rejects_non_mapping_wrong_term_and_invalid_rows_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            invalid_payloads = [None, [], True, {**self.valid_payload(), "term": "wrong"}]
            for value in (None, True, {}, (), "rows"):
                invalid_payloads.append({**self.valid_payload(), "rows": value})
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    self.assert_rejected(payload, config)

    def test_requires_explicitly_present_empty_errors_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            for value in (None, False, {}, (), ["failed"]):
                payload = self.valid_payload()
                payload["errors"] = value
                with self.subTest(value=value):
                    self.assert_rejected(payload, config)

            payload = self.valid_payload()
            del payload["errors"]
            self.assert_rejected(payload, config)

    def test_requires_all_validation_lists_to_be_explicit_and_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            keys = ("missingCourseCodes", "missingDetailLinks", "suspiciousPages")
            for key in keys:
                for value in (None, False, {}, (), ["bad"]):
                    payload = self.valid_payload()
                    payload["validation"][key] = value
                    with self.subTest(key=key, value=value):
                        self.assert_rejected(payload, config)
                payload = self.valid_payload()
                del payload["validation"][key]
                with self.subTest(key=key, value="missing"):
                    self.assert_rejected(payload, config)

            for value in (None, False, [], "validation"):
                payload = self.valid_payload()
                payload["validation"] = value
                with self.subTest(validation=value):
                    self.assert_rejected(payload, config)

    def test_requires_consistent_stats_total_and_duplicate_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            mutations = (
                (("stats",), None),
                (("stats", "专业课"), True),
                (("stats", "专业课"), -1),
                (("stats", "专业课"), 0),
                (("validation", "totalRows"), True),
                (("validation", "totalRows"), 2),
                (("validation", "duplicateSeqs"), None),
                (("validation", "duplicateSeqs"), ["unexpected"]),
                (("validation", "duplicateKeys"), None),
                (("validation", "duplicateKeys"), ["duplicate"]),
            )
            for path, value in mutations:
                payload = self.valid_payload()
                if len(path) == 1:
                    payload[path[0]] = value
                else:
                    payload[path[0]][path[1]] = value
                with self.subTest(path=path, value=value):
                    self.assert_rejected(payload, config)

            for missing_key in ("专业课", "公选课"):
                payload = self.valid_payload()
                del payload["stats"][missing_key]
                with self.subTest(missing_stats_key=missing_key):
                    self.assert_rejected(payload, config)

            payload = self.valid_payload()
            payload["stats"]["unexpected"] = 0
            self.assert_rejected(payload, config)

    def test_rejects_minimal_rows_and_incomplete_row_schemas(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            minimal = {
                "数据学期": TERM,
                "基本信息": {"课程号": "001"},
            }
            payload = self.valid_payload()
            payload["rows"] = [minimal]
            self.assert_rejected(payload, config)

            mutations = (
                ((), None),
                (("数据学期",), "wrong"),
                (("基本信息",), None),
                (("基本信息",), []),
                (("基本信息", "课程号"), None),
                (("基本信息", "课程号"), True),
                (("基本信息", "课程号"), "  \t"),
                (("基本信息", "课程名"), None),
                (("详情链接",), None),
                (("课程序号",), ""),
                (("详细信息",), None),
                (("详细信息",), []),
                (("详细信息",), False),
            )
            for path, value in mutations:
                payload = self.valid_payload()
                if not path:
                    payload["rows"][0] = value
                elif len(path) == 1:
                    payload["rows"][0][path[0]] = value
                else:
                    payload["rows"][0][path[0]][path[1]] = value
                with self.subTest(path=path, value=value):
                    self.assert_rejected(payload, config)

            payload = self.valid_payload()
            del payload["rows"][0]["基本信息"]["课程名"]
            self.assert_rejected(payload, config)

            payload = self.valid_payload()
            del payload["rows"][0]["详细信息"]["教学评估"]
            self.assert_rejected(payload, config)

            payload = self.valid_payload()
            payload["rows"][0]["详细信息"]["unexpected"] = ""
            self.assert_rejected(payload, config)

    def test_rejects_invalid_course_type_detail_url_and_sequence_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            for value in (None, "", "未知类型"):
                payload = self.valid_payload()
                payload["rows"][0]["课程类型"] = value
                with self.subTest(course_type=value):
                    self.assert_rejected(payload, config)

            invalid_links = (
                "http://elective.pku.edu.cn/elective2008/edu/pku/stu/elective/"
                "controller/courseQuery/goNested.do?course_seq_no=SEQ001",
                "https://example.com/elective2008/edu/pku/stu/elective/controller/"
                "courseQuery/goNested.do?course_seq_no=SEQ001",
                "https://elective.pku.edu.cn/wrong?course_seq_no=SEQ001",
                "https://elective.pku.edu.cn/elective2008/edu/pku/stu/elective/"
                "controller/courseQuery/goNested.do?course_seq_no=OTHER",
            )
            for link in invalid_links:
                payload = self.valid_payload()
                payload["rows"][0]["详情链接"] = link
                with self.subTest(link=link):
                    self.assert_rejected(payload, config)

    def test_allows_consistent_cross_registration_duplicate_sequences(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            rows = [
                self.undergrad_row(
                    course_type="专业课",
                    course_code="001",
                    sequence="SHARED001",
                ),
                self.undergrad_row(
                    course_type="公选课",
                    course_code="001",
                    sequence="SHARED001",
                ),
            ]
            payload = self.valid_payload(rows)

            validated = validate_payload(payload, config)

            self.assertIs(validated, rows)
            self.assertEqual(payload["validation"]["duplicateSeqs"], ["SHARED001"])
            self.assertEqual(len(validated), 2)
            self.assertEqual(
                [row["课程类型"] for row in validated],
                ["专业课", "公选课"],
            )

    def test_rejects_inconsistent_duplicate_sequence_list_and_duplicate_unique_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            cross_registered = [
                self.undergrad_row(course_type="专业课", sequence="SHARED001"),
                self.undergrad_row(course_type="公选课", sequence="SHARED001"),
            ]
            payload = self.valid_payload(cross_registered)
            payload["validation"]["duplicateSeqs"] = []
            self.assert_rejected(payload, config)

            duplicate_key_rows = [
                self.undergrad_row(sequence="SEQ001"),
                self.undergrad_row(sequence="SEQ002"),
            ]
            self.assert_rejected(self.valid_payload(duplicate_key_rows), config)


class ReceiverPublishingArchiveTests(ReceiverTestCase):
    def test_rejected_body_is_archived_exactly_without_replacing_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            config.final_output.parent.mkdir(parents=True)
            config.final_output.write_bytes(b"official")
            os.chmod(config.final_output, 0o640)
            body = b'{"term":"wrong","rows":[]}'

            with self.assertRaises(PayloadRejected):
                publish_payload(body, config)

            self.assertEqual(config.raw_output.read_bytes(), body)
            self.assertEqual(config.final_output.read_bytes(), b"official")
            self.assertEqual(stat.S_IMODE(config.final_output.stat().st_mode), 0o640)
            self.assertEqual(
                sorted(path.name for path in Path(tmp).rglob("*") if path.is_file()),
                ["final.json", "raw.json"],
            )


class ReceiverHTTPTests(ReceiverTestCase):
    token = "deterministic-test-token"

    @contextlib.contextmanager
    def running_server(self, config: ReceiverConfig, handler_transform=None):
        config.script.parent.mkdir(parents=True, exist_ok=True)
        config.script.write_text(
            'const receiver = "__RECEIVER_URL__";\n'
            'const token = "__RECEIVER_TOKEN__";\n',
            encoding="utf-8",
        )
        handler = make_handler(config, self.token)
        if handler_transform is not None:
            handler = handler_transform(handler)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())

    def request(
        self,
        server: ThreadingHTTPServer,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            server.server_port,
            timeout=2,
        )
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        response_body = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        status = response.status
        connection.close()
        return status, response_headers, response_body

    def actual_headers(self, *, token: str | None = None) -> dict[str, str]:
        headers = {"Origin": PKU_ORIGIN}
        if token is not None:
            headers[TOKEN_HEADER] = token
        return headers

    def assert_fixed_cors(self, headers: dict[str, str]) -> None:
        self.assertEqual(headers.get("access-control-allow-origin"), PKU_ORIGIN)
        self.assertEqual(headers.get("vary"), "Origin")
        self.assertNotEqual(headers.get("access-control-allow-origin"), "*")

    def raw_request(self, server: ThreadingHTTPServer, request: bytes) -> tuple[int, bytes]:
        with socket.create_connection(("127.0.0.1", server.server_port), timeout=2) as sock:
            sock.sendall(request)
            sock.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        response = b"".join(chunks)
        status = int(response.split(b"\r\n", 1)[0].split()[1])
        return status, response

    def test_preflight_authenticates_origin_path_method_and_header_names_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with self.running_server(config) as server:
                headers = {
                    "Origin": PKU_ORIGIN,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": f"content-type, {TOKEN_HEADER}",
                }
                status, response_headers, _ = self.request(
                    server,
                    "OPTIONS",
                    "/done",
                    headers=headers,
                )
                self.assertEqual(status, 204)
                self.assert_fixed_cors(response_headers)
                self.assertIn(TOKEN_HEADER, response_headers["access-control-allow-headers"])

                for changed, expected in (
                    ({**headers, "Origin": "https://example.com"}, 403),
                    ({**headers, "Access-Control-Request-Method": "GET"}, 403),
                    ({**headers, "Access-Control-Request-Headers": "X-Unknown"}, 403),
                ):
                    with self.subTest(headers=changed):
                        status, response_headers, _ = self.request(
                            server,
                            "OPTIONS",
                            "/done",
                            headers=changed,
                        )
                        self.assertEqual(status, expected)
                        self.assert_fixed_cors(response_headers)

                status, _, _ = self.request(
                    server,
                    "OPTIONS",
                    "/unknown",
                    headers=headers,
                )
                self.assertEqual(status, 404)

    def test_private_network_preflight_header_is_emitted_only_after_full_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with self.running_server(config) as server:
                headers = {
                    "Origin": PKU_ORIGIN,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": f"content-type, {TOKEN_HEADER}",
                    "Access-Control-Request-Private-Network": "true",
                }
                status, response_headers, _ = self.request(
                    server,
                    "OPTIONS",
                    "/done",
                    headers=headers,
                )
                self.assertEqual(status, 204)
                self.assert_fixed_cors(response_headers)
                self.assertEqual(
                    response_headers.get("access-control-allow-private-network"),
                    "true",
                )

                invalid_cases = (
                    ("/done", {**headers, "Origin": "https://example.com"}),
                    ("/unknown", headers),
                    (
                        "/done",
                        {**headers, "Access-Control-Request-Method": "GET"},
                    ),
                    (
                        "/done",
                        {**headers, "Access-Control-Request-Headers": "X-Unknown"},
                    ),
                    (
                        "/done",
                        {
                            **headers,
                            "Access-Control-Request-Private-Network": "false",
                        },
                    ),
                )
                for path, changed_headers in invalid_cases:
                    with self.subTest(path=path, headers=changed_headers):
                        status, response_headers, _ = self.request(
                            server,
                            "OPTIONS",
                            path,
                            headers=changed_headers,
                        )
                        self.assertNotEqual(status, 204)
                        self.assertNotIn(
                            "access-control-allow-private-network",
                            response_headers,
                        )

    def test_actual_get_requires_exact_origin_and_compare_digest_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with self.running_server(config) as server:
                cases = (
                    ({TOKEN_HEADER: self.token}, 403),
                    (self.actual_headers(), 403),
                    (self.actual_headers(token="wrong"), 403),
                )
                for headers, expected in cases:
                    with self.subTest(headers=headers):
                        status, response_headers, body = self.request(
                            server,
                            "GET",
                            "/inpage.js",
                            headers=headers,
                        )
                        self.assertEqual(status, expected)
                        self.assert_fixed_cors(response_headers)
                        self.assertNotIn(self.token.encode(), body)
                        self.assertNotIn(b"Traceback", body)

                with mock.patch.object(
                    receiver_common.hmac,
                    "compare_digest",
                    wraps=receiver_common.hmac.compare_digest,
                ) as compare_digest:
                    status, _, _ = self.request(
                        server,
                        "GET",
                        "/inpage.js",
                        headers=self.actual_headers(token=self.token),
                    )
                    self.assertEqual(status, 200)
                    compare_digest.assert_called_with(
                        self.token.encode(),
                        self.token.encode(),
                    )

    def test_script_serving_injects_bound_url_and_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with self.running_server(config) as server:
                status, headers, body = self.request(
                    server,
                    "GET",
                    "/inpage.js",
                    headers=self.actual_headers(token=self.token),
                )
                self.assertEqual(status, 200)
                self.assert_fixed_cors(headers)
                self.assertEqual(headers["content-type"], "application/javascript; charset=utf-8")
                code = body.decode("utf-8")
                self.assertIn(f"http://127.0.0.1:{server.server_port}", code)
                self.assertIn(self.token, code)
                self.assertNotIn("__RECEIVER_URL__", code)
                self.assertNotIn("__RECEIVER_TOKEN__", code)

    def test_progress_requires_auth_and_a_modest_json_object_without_writing_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with self.running_server(config) as server:
                body = b'{"stage":"list","rows":3}'
                headers = {
                    **self.actual_headers(token=self.token),
                    "Content-Type": "application/json",
                }
                for changed in (
                    {**headers, "Origin": "https://example.com"},
                    {key: value for key, value in headers.items() if key != TOKEN_HEADER},
                ):
                    status, _, _ = self.request(
                        server,
                        "POST",
                        "/progress",
                        body=body,
                        headers=changed,
                    )
                    self.assertEqual(status, 403)

                for invalid in (b"[]", b"true", b"{"):
                    status, _, _ = self.request(
                        server,
                        "POST",
                        "/progress",
                        body=invalid,
                        headers=headers,
                    )
                    self.assertEqual(status, 400)

                status, _, response = self.request(
                    server,
                    "POST",
                    "/progress",
                    body=body,
                    headers=headers,
                )
                self.assertEqual(status, 200)
                self.assertEqual(response, b"ok\n")
                self.assertFalse(config.raw_output.exists())
                self.assertFalse(config.final_output.exists())

                oversized_headers = {
                    **headers,
                    "Content-Length": str(MAX_PROGRESS_BYTES + 1),
                }
                status, _, _ = self.request(
                    server,
                    "POST",
                    "/progress",
                    headers=oversized_headers,
                )
                self.assertEqual(status, 413)

    def test_rejects_oversized_length_before_body_is_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with self.running_server(config) as server:
                status, _, _ = self.request(
                    server,
                    "POST",
                    "/done",
                    headers={
                        **self.actual_headers(token=self.token),
                        "Content-Length": str(MAX_BODY_BYTES + 1),
                    },
                )
                self.assertEqual(status, 413)
                self.assertFalse(config.raw_output.exists())

    def test_rejects_missing_malformed_negative_duplicate_and_transfer_lengths(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with self.running_server(config) as server:
                common = (
                    f"Host: 127.0.0.1:{server.server_port}\r\n"
                    f"Origin: {PKU_ORIGIN}\r\n"
                    f"{TOKEN_HEADER}: {self.token}\r\n"
                    "Connection: close\r\n"
                )
                cases = {
                    "missing": common,
                    "malformed": common + "Content-Length: nope\r\n",
                    "negative": common + "Content-Length: -1\r\n",
                    "duplicate": common + "Content-Length: 1\r\nContent-Length: 1\r\n",
                    "transfer": common + "Transfer-Encoding: chunked\r\n",
                }
                for name, headers in cases.items():
                    request = f"POST /done HTTP/1.1\r\n{headers}\r\n".encode("ascii")
                    with self.subTest(name=name):
                        status, response = self.raw_request(server, request)
                        self.assertIn(status, (400, 411))
                        self.assertNotIn(self.token.encode(), response)
                        self.assertNotIn(b"Traceback", response)

    def test_rejects_pathological_numeric_length_without_handler_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with self.running_server(config) as server:
                request = (
                    "POST /done HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{server.server_port}\r\n"
                    f"Origin: {PKU_ORIGIN}\r\n"
                    f"{TOKEN_HEADER}: {self.token}\r\n"
                    f"Content-Length: {'9' * 5000}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
                status, response = self.raw_request(server, request)
                self.assertEqual(status, 413)
                self.assertNotIn(b"Traceback", response)

    def test_rejects_non_ascii_token_header_without_handler_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with self.running_server(config) as server:
                request = (
                    "GET /inpage.js HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{server.server_port}\r\n"
                    f"Origin: {PKU_ORIGIN}\r\n"
                    f"{TOKEN_HEADER}: "
                ).encode("ascii") + b"\xff\r\nConnection: close\r\n\r\n"
                status, response = self.raw_request(server, request)
                self.assertEqual(status, 403)
                self.assertNotIn(self.token.encode(), response)
                self.assertNotIn(b"Traceback", response)

    def test_rejects_truncated_body_and_unknown_paths_with_controlled_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with self.running_server(config) as server:
                request = (
                    "POST /done HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{server.server_port}\r\n"
                    f"Origin: {PKU_ORIGIN}\r\n"
                    f"{TOKEN_HEADER}: {self.token}\r\n"
                    "Content-Length: 10\r\n"
                    "Connection: close\r\n\r\n"
                    "{}"
                ).encode("ascii")
                status, response = self.raw_request(server, request)
                self.assertEqual(status, 400)
                self.assertNotIn(b"Traceback", response)

                status, _, _ = self.request(
                    server,
                    "POST",
                    "/unknown",
                    body=b"{}",
                    headers=self.actual_headers(token=self.token),
                )
                self.assertEqual(status, 404)

                status, _, _ = self.request(
                    server,
                    "GET",
                    "/unknown",
                    headers=self.actual_headers(token=self.token),
                )
                self.assertEqual(status, 404)

    def test_open_partial_body_times_out_without_archiving_or_publishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            with self.running_server(config) as server:
                request = (
                    "POST /done HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{server.server_port}\r\n"
                    f"Origin: {PKU_ORIGIN}\r\n"
                    f"{TOKEN_HEADER}: {self.token}\r\n"
                    "Content-Length: 10\r\n"
                    "Connection: keep-alive\r\n\r\n"
                ).encode("ascii")
                with (
                    mock.patch.object(
                        receiver_common,
                        "BODY_READ_TIMEOUT_SECONDS",
                        0.15,
                        create=True,
                    ),
                    socket.create_connection(
                        ("127.0.0.1", server.server_port),
                        timeout=1,
                    ) as sock,
                ):
                    started = time.monotonic()
                    sock.sendall(request)
                    response = sock.recv(4096)
                    elapsed = time.monotonic() - started

                self.assertEqual(
                    int(response.split(b"\r\n", 1)[0].split()[1]),
                    408,
                )
                self.assertLess(elapsed, 0.8)
                self.assertFalse(config.raw_output.exists())
                self.assertFalse(config.final_output.exists())

    def test_invalid_done_stays_alive_for_valid_retry_then_publishes_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            config.final_output.parent.mkdir(parents=True)
            config.final_output.write_bytes(b"official")
            headers = {
                **self.actual_headers(token=self.token),
                "Content-Type": "application/json",
            }
            with self.running_server(config) as server:
                invalid = self.valid_payload()
                invalid["term"] = "wrong"
                invalid_body = json.dumps(invalid, ensure_ascii=False).encode()
                status, _, response = self.request(
                    server,
                    "POST",
                    "/done",
                    body=invalid_body,
                    headers=headers,
                )
                self.assertEqual(status, 422)
                self.assertEqual(config.raw_output.read_bytes(), invalid_body)
                self.assertEqual(config.final_output.read_bytes(), b"official")
                self.assertNotIn(self.token.encode(), response)

                valid = self.valid_payload()
                valid_body = json.dumps(valid, ensure_ascii=False).encode()
                status, _, response = self.request(
                    server,
                    "POST",
                    "/done",
                    body=valid_body,
                    headers=headers,
                )
                self.assertEqual(status, 200)
                self.assertEqual(response, b"ok\n")
                self.assertEqual(config.raw_output.read_bytes(), valid_body)
                self.assertEqual(
                    json.loads(config.final_output.read_text(encoding="utf-8")),
                    valid["rows"],
                )

    def test_success_requests_shutdown_before_response_delivery_failure(self):
        def fail_success_response(base_handler):
            class FailingResponseHandler(base_handler):
                def _respond(self, status, body=b"", content_type="text/plain; charset=utf-8"):
                    if status == 200 and self.path == "/done":
                        raise OSError("simulated response delivery failure")
                    return super()._respond(status, body, content_type)

            return FailingResponseHandler

        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            headers = {
                **self.actual_headers(token=self.token),
                "Content-Type": "application/json",
            }
            body = json.dumps(self.valid_payload(), ensure_ascii=False).encode()
            with self.running_server(config, fail_success_response) as server:
                shutdown_requested = threading.Event()
                real_shutdown = server.shutdown

                def observe_shutdown():
                    shutdown_requested.set()
                    real_shutdown()

                server.shutdown = observe_shutdown
                try:
                    with self.assertRaises((OSError, http.client.HTTPException)):
                        self.request(server, "POST", "/done", body=body, headers=headers)
                    self.assertTrue(shutdown_requested.wait(0.5))
                finally:
                    server.shutdown = real_shutdown

            self.assertEqual(config.raw_output.read_bytes(), body)
            self.assertEqual(
                json.loads(config.final_output.read_text(encoding="utf-8")),
                self.valid_payload()["rows"],
            )

    def test_concurrent_done_requests_publish_only_one_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            headers = {
                **self.actual_headers(token=self.token),
                "Content-Type": "application/json",
            }
            payloads = (
                self.valid_payload(
                    [self.undergrad_row(course_code="001", sequence="SEQ001")]
                ),
                self.valid_payload(
                    [self.undergrad_row(course_code="002", sequence="SEQ002")]
                ),
            )
            bodies = tuple(
                json.dumps(payload, ensure_ascii=False).encode() for payload in payloads
            )
            with self.running_server(config) as server:
                real_shutdown = server.shutdown
                shutdown_requested = threading.Event()
                server.shutdown = shutdown_requested.set
                entered_publish = threading.Event()
                release_publish = threading.Event()
                published_bodies = []
                real_publish = receiver_common.publish_payload

                def blocking_publish(body, receiver_config):
                    published_bodies.append(body)
                    entered_publish.set()
                    release_publish.wait(2)
                    return real_publish(body, receiver_config)

                results = {}

                def post_done(name, body):
                    results[name] = self.request(
                        server,
                        "POST",
                        "/done",
                        body=body,
                        headers=headers,
                    )

                try:
                    with mock.patch.object(
                        receiver_common,
                        "publish_payload",
                        side_effect=blocking_publish,
                    ):
                        first = threading.Thread(
                            target=post_done,
                            args=("first", bodies[0]),
                        )
                        second = threading.Thread(
                            target=post_done,
                            args=("second", bodies[1]),
                        )
                        first.start()
                        self.assertTrue(entered_publish.wait(1))
                        second.start()
                        time.sleep(0.05)
                        release_publish.set()
                        first.join(timeout=2)
                        second.join(timeout=2)
                        self.assertFalse(first.is_alive())
                        self.assertFalse(second.is_alive())

                    self.assertEqual(
                        sorted(result[0] for result in results.values()),
                        [200, 409],
                    )
                    self.assertEqual(len(published_bodies), 1)
                    self.assertTrue(shutdown_requested.wait(0.5))
                    self.assertEqual(config.raw_output.read_bytes(), published_bodies[0])
                finally:
                    release_publish.set()
                    server.shutdown = real_shutdown


class ReceiverPublishingAtomicityTests(ReceiverTestCase):
    def fail_first_final_directory_fsync(self, config: ReceiverConfig):
        real_fsync = receiver_common._directory_fsync
        failed = False

        def fail_once(directory):
            nonlocal failed
            if Path(directory) == config.final_output.parent and not failed:
                failed = True
                raise OSError("simulated final directory fsync failure")
            return real_fsync(directory)

        return fail_once

    def test_invalid_utf8_and_json_are_archived_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            for body in (b"\xff", b"{"):
                with self.subTest(body=body):
                    with self.assertRaises(PayloadRejected):
                        publish_payload(body, config)
                    self.assertEqual(config.raw_output.read_bytes(), body)
                    self.assertFalse(config.final_output.exists())

    def test_nonstandard_json_constants_are_archived_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            payload = self.valid_payload()
            body = (
                json.dumps(payload)
                .replace('"term"', '"extra": NaN, "term"', 1)
                .encode()
            )

            with self.assertRaises(PayloadRejected):
                publish_payload(body, config)

            self.assertEqual(config.raw_output.read_bytes(), body)
            self.assertFalse(config.final_output.exists())

    def test_valid_publish_writes_pretty_rows_with_newline_and_preserves_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            config.final_output.parent.mkdir(parents=True)
            config.final_output.write_bytes(b"old")
            os.chmod(config.final_output, 0o640)
            payload = self.valid_payload()
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()

            rows = publish_payload(body, config)

            expected = (
                json.dumps(payload["rows"], ensure_ascii=False, indent=2) + "\n"
            ).encode()
            self.assertEqual(rows, payload["rows"])
            self.assertEqual(config.raw_output.read_bytes(), body)
            self.assertEqual(config.final_output.read_bytes(), expected)
            self.assertEqual(stat.S_IMODE(config.final_output.stat().st_mode), 0o640)
            self.assertEqual(stat.S_IMODE(config.raw_output.stat().st_mode), 0o644)

    def test_new_outputs_use_readable_default_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            body = json.dumps(self.valid_payload(), ensure_ascii=False).encode()

            publish_payload(body, config)

            self.assertEqual(stat.S_IMODE(config.raw_output.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(config.final_output.stat().st_mode), 0o644)

    def test_final_replace_failure_keeps_existing_bytes_mode_and_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            config.final_output.parent.mkdir(parents=True)
            config.final_output.write_bytes(b"official")
            os.chmod(config.final_output, 0o600)
            body = json.dumps(self.valid_payload(), ensure_ascii=False).encode()
            real_replace = os.replace

            def fail_final_replace(source, destination):
                if Path(destination) == config.final_output:
                    raise OSError("simulated final replace failure")
                return real_replace(source, destination)

            with mock.patch.object(receiver_common.os, "replace", side_effect=fail_final_replace):
                with self.assertRaises(OSError):
                    publish_payload(body, config)

            self.assertEqual(config.raw_output.read_bytes(), body)
            self.assertEqual(config.final_output.read_bytes(), b"official")
            self.assertEqual(stat.S_IMODE(config.final_output.stat().st_mode), 0o600)
            self.assertEqual(
                sorted(path.name for path in Path(tmp).rglob("*") if path.is_file()),
                ["final.json", "raw.json"],
            )

    def test_final_directory_fsync_failure_restores_existing_bytes_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            config.final_output.parent.mkdir(parents=True)
            config.final_output.write_bytes(b"official")
            os.chmod(config.final_output, 0o640)
            body = json.dumps(self.valid_payload(), ensure_ascii=False).encode()

            with mock.patch.object(
                receiver_common,
                "_directory_fsync",
                side_effect=self.fail_first_final_directory_fsync(config),
            ):
                with self.assertRaisesRegex(OSError, "final directory fsync"):
                    publish_payload(body, config)

            self.assertEqual(config.raw_output.read_bytes(), body)
            self.assertEqual(config.final_output.read_bytes(), b"official")
            self.assertEqual(stat.S_IMODE(config.final_output.stat().st_mode), 0o640)
            self.assertEqual(
                sorted(path.name for path in Path(tmp).rglob("*") if path.is_file()),
                ["final.json", "raw.json"],
            )

    def test_final_directory_fsync_failure_removes_new_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            body = json.dumps(self.valid_payload(), ensure_ascii=False).encode()

            with mock.patch.object(
                receiver_common,
                "_directory_fsync",
                side_effect=self.fail_first_final_directory_fsync(config),
            ):
                with self.assertRaisesRegex(OSError, "final directory fsync"):
                    publish_payload(body, config)

            self.assertEqual(config.raw_output.read_bytes(), body)
            self.assertFalse(config.final_output.exists())
            self.assertEqual(
                sorted(path.name for path in Path(tmp).rglob("*") if path.is_file()),
                ["raw.json"],
            )

    def test_failed_backup_restore_preserves_original_backup_for_manual_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            config.final_output.parent.mkdir(parents=True)
            config.final_output.write_bytes(b"official")
            os.chmod(config.final_output, 0o640)
            body = json.dumps(self.valid_payload(), ensure_ascii=False).encode()
            real_replace = os.replace

            def fail_backup_restore(source, destination):
                if (
                    Path(source).suffix == ".backup"
                    and Path(destination) == config.final_output
                ):
                    raise OSError("simulated backup restore failure")
                return real_replace(source, destination)

            with (
                mock.patch.object(
                    receiver_common,
                    "_directory_fsync",
                    side_effect=self.fail_first_final_directory_fsync(config),
                ),
                mock.patch.object(
                    receiver_common.os,
                    "replace",
                    side_effect=fail_backup_restore,
                ),
            ):
                with self.assertRaisesRegex(OSError, "backup restore failure"):
                    publish_payload(body, config)

            backups = list(config.final_output.parent.glob("*.backup"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"official")
            self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o640)
            self.assertEqual(config.raw_output.read_bytes(), body)

    def test_post_commit_backup_cleanup_failure_does_not_report_publish_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            config.final_output.parent.mkdir(parents=True)
            config.final_output.write_bytes(b"official")
            body = json.dumps(self.valid_payload(), ensure_ascii=False).encode()
            real_unlink = Path.unlink
            backup_unlinks = 0

            def fail_first_cleanup(path, *args, **kwargs):
                nonlocal backup_unlinks
                if path.suffix == ".backup":
                    backup_unlinks += 1
                    if backup_unlinks == 2:
                        raise OSError("simulated post-commit cleanup failure")
                return real_unlink(path, *args, **kwargs)

            with mock.patch.object(Path, "unlink", new=fail_first_cleanup):
                rows = publish_payload(body, config)

            self.assertEqual(rows, self.valid_payload()["rows"])
            self.assertEqual(
                json.loads(config.final_output.read_text(encoding="utf-8")),
                rows,
            )
            self.assertEqual(list(config.final_output.parent.glob("*.backup")), [])


class ReceiverRunnerTests(ReceiverTestCase):
    def test_run_receiver_binds_loopback_generates_token_and_prints_loader(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            output = io.StringIO()
            captured = {}

            class FakeServer:
                server_port = 9123

                def __init__(self, address, handler):
                    captured["address"] = address
                    captured["handler"] = handler
                    captured["served"] = False
                    captured["closed"] = False

                def serve_forever(self):
                    captured["served"] = True

                def server_close(self):
                    captured["closed"] = True

            with (
                mock.patch.object(
                    receiver_common.secrets,
                    "token_urlsafe",
                    return_value="one-run-token",
                ) as generate,
                mock.patch.object(receiver_common, "ThreadingHTTPServer", FakeServer),
                redirect_stdout(output),
            ):
                run_receiver(config, 8765)

            generate.assert_called_once_with(32)
            self.assertEqual(captured["address"], ("127.0.0.1", 8765))
            self.assertTrue(captured["served"])
            self.assertTrue(captured["closed"])
            instructions = output.getvalue()
            self.assertIn("fetch(\"http://127.0.0.1:9123/inpage.js\"", instructions)
            self.assertIn(TOKEN_HEADER, instructions)
            self.assertIn("one-run-token", instructions)
            self.assertIn("document.createElement('script')", instructions)


class ReceiverEntrypointTests(unittest.TestCase):
    MODULE_CONFIGS = (
        (
            "receive_pku_summer_payload",
            "pku_inpage_summer_scraper.js",
            "tmp_summer/inpage_payload.json",
            "课程数据/北大暑期课程_25-26第3学期.json",
            "25-26学年第3学期",
            "PKU summer-course receiver",
        ),
        (
            "receive_pku_undergrad_2627_fall_payload",
            "pku_inpage_undergrad_2627_fall_scraper.js",
            "tmp_undergrad_2627_fall/inpage_payload.json",
            "课程数据/北大本科课程_26-27第1学期.json",
            TERM,
            "PKU 26-27 fall undergrad-course receiver",
        ),
        (
            "receive_pku_graduate_2627_fall_payload",
            "pku_inpage_graduate_2627_fall_scraper.js",
            "tmp_graduate_2627_fall/inpage_payload.json",
            "课程数据/北大研究生课程_26-27第1学期.json",
            TERM,
            "PKU 26-27 fall graduate-course receiver",
        ),
    )

    def test_all_three_module_configs_preserve_paths_terms_and_labels(self):
        project_root = Path(__file__).resolve().parents[1]
        scraper_dir = project_root / "北京大学选课网数据抓取"
        for module_name, script, raw, final, term, label in self.MODULE_CONFIGS:
            with self.subTest(module=module_name):
                module = importlib.import_module(f"北京大学选课网数据抓取.{module_name}")
                config = module.CONFIG
                self.assertEqual(config.script, scraper_dir / script)
                self.assertEqual(config.raw_output, project_root / raw)
                self.assertEqual(config.final_output, project_root / final)
                self.assertEqual(config.term, term)
                self.assertEqual(config.label, label)

    def test_all_three_main_functions_forward_default_and_custom_ports(self):
        for module_name, *_ in self.MODULE_CONFIGS:
            module = importlib.import_module(f"北京大学选课网数据抓取.{module_name}")
            with (
                self.subTest(module=module_name),
                mock.patch.object(module, "run_receiver") as run,
            ):
                module.main([])
                run.assert_called_once_with(module.CONFIG, 8765)
                run.reset_mock()
                module.main(["--port", "9123"])
                run.assert_called_once_with(module.CONFIG, 9123)

    def test_all_three_configs_carry_expected_scraper_schema_contracts(self):
        for index, (module_name, *_) in enumerate(self.MODULE_CONFIGS):
            module = importlib.import_module(f"北京大学选课网数据抓取.{module_name}")
            config = module.CONFIG
            with self.subTest(module=module_name):
                if index < 2:
                    self.assertEqual(config.level, "undergraduate")
                    self.assertEqual(config.course_types, ALL_UNDERGRAD_COURSE_TYPES)
                    self.assertIsNone(config.stats_bucket)
                    self.assertEqual(config.basic_fields, UNDERGRAD_BASIC_FIELDS)
                    self.assertEqual(config.optional_basic_fields, ("英语等级",))
                    self.assertEqual(config.detail_fields, UNDERGRAD_DETAIL_FIELDS)
                    expected_unique_key = (
                        ("课程类型", "课程号", "班号")
                        if index == 0
                        else ("课程类型", "课程号", "班号", "教师")
                    )
                    self.assertEqual(config.unique_key_fields, expected_unique_key)
                else:
                    self.assertEqual(config.level, "graduate")
                    self.assertEqual(config.course_types, ())
                    self.assertEqual(config.stats_bucket, "研究生课")
                    self.assertEqual(config.basic_fields, GRADUATE_BASIC_FIELDS)
                    self.assertEqual(config.optional_basic_fields, ())
                    self.assertEqual(config.detail_fields, GRADUATE_DETAIL_FIELDS)
                    self.assertEqual(
                        config.unique_key_fields,
                        ("课程号", "班号", "教师", "开课单位"),
                    )

    def test_all_three_scripts_support_help_without_starting_a_server(self):
        project_root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            for module_name, *_ in self.MODULE_CONFIGS:
                script = project_root / "北京大学选课网数据抓取" / f"{module_name}.py"
                with self.subTest(script=script.name):
                    result = subprocess.run(
                        [sys.executable, str(script), "--help"],
                        cwd=tmp,
                        text=True,
                        capture_output=True,
                        timeout=5,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("--port", result.stdout)


if __name__ == "__main__":
    unittest.main()

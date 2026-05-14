#!/usr/bin/env python3
"""Receive PKU summer-course JSON from the in-page scraper.

The server binds to 127.0.0.1 only. It serves pku_inpage_summer_scraper.js
from this archive folder and writes scraped output back to the project root.
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SCRIPT = SCRIPT_DIR / "pku_inpage_summer_scraper.js"
RAW_OUT = PROJECT_ROOT / "tmp_summer" / "inpage_payload.json"
FINAL_OUT = PROJECT_ROOT / "课程数据" / "北大暑期课程_25-26第3学期.json"


class Handler(BaseHTTPRequestHandler):
    server_version = "PkuSummerReceiver/1.0"

    def log_message(self, fmt: str, *args):
        print(f"[receiver] {self.address_string()} - {fmt % args}", flush=True)

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/inpage.js":
            self.send_error(404)
            return
        receiver_url = f"http://127.0.0.1:{self.server.server_port}"
        code = SCRIPT.read_text(encoding="utf-8").replace("__RECEIVER_URL__", receiver_url)
        data = code.encode("utf-8")
        self.send_response(200)
        self.cors()
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        if parsed.path == "/progress":
            try:
                payload = json.loads(body.decode("utf-8"))
                print(f"[progress] {json.dumps(payload, ensure_ascii=False)}", flush=True)
            except Exception as exc:
                print(f"[progress] unparseable: {exc}", flush=True)
            self.send_response(200)
            self.cors()
            self.end_headers()
            self.wfile.write(b"ok")
            return

        if parsed.path != "/done":
            self.send_error(404)
            return

        RAW_OUT.parent.mkdir(parents=True, exist_ok=True)
        FINAL_OUT.parent.mkdir(parents=True, exist_ok=True)
        RAW_OUT.write_bytes(body)

        payload = json.loads(body.decode("utf-8"))
        rows = payload.get("rows", [])
        FINAL_OUT.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[done] raw payload: {RAW_OUT}", flush=True)
        print(f"[done] final json : {FINAL_OUT}", flush=True)
        print(f"[done] rows       : {len(rows)}", flush=True)
        print(f"[done] stats      : {json.dumps(payload.get('stats', {}), ensure_ascii=False)}", flush=True)
        if payload.get("errors"):
            print(f"[done] errors     : {json.dumps(payload['errors'], ensure_ascii=False)}", flush=True)

        self.send_response(200)
        self.cors()
        self.end_headers()
        self.wfile.write(b"ok")
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[receiver] serving http://127.0.0.1:{args.port}/inpage.js", flush=True)
    print("[receiver] waiting for /done ...", flush=True)
    server.serve_forever()
    print("[receiver] stopped", flush=True)


if __name__ == "__main__":
    main()

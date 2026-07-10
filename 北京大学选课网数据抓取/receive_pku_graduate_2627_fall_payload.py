#!/usr/bin/env python3
"""Receive PKU 26-27 fall graduate-course JSON from the in-page scraper."""
from __future__ import annotations

import argparse
from pathlib import Path

try:
    from .receiver_common import (
        GRADUATE_BASIC_FIELDS,
        GRADUATE_DETAIL_FIELDS,
        ReceiverConfig,
        run_receiver,
    )
except ImportError:  # Direct execution adds this script directory to sys.path.
    from receiver_common import (
        GRADUATE_BASIC_FIELDS,
        GRADUATE_DETAIL_FIELDS,
        ReceiverConfig,
        run_receiver,
    )


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SCRIPT = SCRIPT_DIR / "pku_inpage_graduate_2627_fall_scraper.js"
RAW_OUT = PROJECT_ROOT / "tmp_graduate_2627_fall" / "inpage_payload.json"
FINAL_OUT = PROJECT_ROOT / "课程数据" / "北大研究生课程_26-27第1学期.json"

CONFIG = ReceiverConfig(
    script=SCRIPT,
    raw_output=RAW_OUT,
    final_output=FINAL_OUT,
    term="26-27学年第1学期",
    label="PKU 26-27 fall graduate-course receiver",
    level="graduate",
    course_types=(),
    stats_bucket="研究生课",
    basic_fields=GRADUATE_BASIC_FIELDS,
    optional_basic_fields=(),
    detail_fields=GRADUATE_DETAIL_FIELDS,
    unique_key_fields=("课程号", "班号", "教师", "开课单位"),
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    run_receiver(CONFIG, args.port)


if __name__ == "__main__":
    main()

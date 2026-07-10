#!/usr/bin/env python3
"""Receive PKU summer-course JSON from the in-page scraper."""
from __future__ import annotations

import argparse
from pathlib import Path

try:
    from .receiver_common import (
        UNDERGRAD_BASIC_FIELDS,
        UNDERGRAD_COURSE_TYPES,
        UNDERGRAD_DETAIL_FIELDS,
        ReceiverConfig,
        run_receiver,
    )
except ImportError:  # Direct execution adds this script directory to sys.path.
    from receiver_common import (
        UNDERGRAD_BASIC_FIELDS,
        UNDERGRAD_COURSE_TYPES,
        UNDERGRAD_DETAIL_FIELDS,
        ReceiverConfig,
        run_receiver,
    )


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SCRIPT = SCRIPT_DIR / "pku_inpage_summer_scraper.js"
RAW_OUT = PROJECT_ROOT / "tmp_summer" / "inpage_payload.json"
FINAL_OUT = PROJECT_ROOT / "课程数据" / "北大暑期课程_25-26第3学期.json"

CONFIG = ReceiverConfig(
    script=SCRIPT,
    raw_output=RAW_OUT,
    final_output=FINAL_OUT,
    term="25-26学年第3学期",
    label="PKU summer-course receiver",
    level="undergraduate",
    course_types=UNDERGRAD_COURSE_TYPES,
    stats_bucket=None,
    basic_fields=UNDERGRAD_BASIC_FIELDS,
    optional_basic_fields=("英语等级",),
    detail_fields=UNDERGRAD_DETAIL_FIELDS,
    unique_key_fields=("课程类型", "课程号", "班号"),
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    run_receiver(CONFIG, args.port)


if __name__ == "__main__":
    main()

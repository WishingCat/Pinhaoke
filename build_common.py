"""Shared helpers for building course SQLite databases from PKU JSON dumps."""
import re

WEEKDAY_ORDER = "一二三四五六日"


def parse_schedule(raw: str):
    """Parse '上课时间及教室' -> (schedule_clean, classroom, weekdays_str).

    The raw field concatenates slots without delimiters, e.g.:
        '1~15周 每周周一10~11节 二教5111~15周 每周周四10~11节 二教511'
    Room digits (511) merge with the next week range (1~15), so a plain regex
    split is unreliable. We extract weekdays and classrooms separately and
    reformat the schedule text for display.
    """
    if not raw:
        return "", "", ""
    text = raw.strip()

    weekdays = set(re.findall(r"周([一二三四五六日])", text))
    weekday_str = ",".join(
        "周" + d for d in sorted(weekdays, key=lambda x: WEEKDAY_ORDER.index(x))
    )

    room_matches = re.findall(
        r"节\s?([一-鿿]{1,6}[A-Za-z]?\d{1,3}[A-Za-z]?)", text
    )
    rooms = sorted(set(room_matches))
    filtered = [
        r for r in rooms if not any(o != r and o.startswith(r) for o in rooms)
    ]
    classroom = ", ".join(filtered)

    schedule = re.sub(
        r"(节)\s*[一-鿿]{1,6}[A-Za-z]?\d{1,3}[A-Za-z]?", r"\1", text
    )
    schedule = re.sub(r"(节)(\d{1,2}~\d{1,2}周)", r"\1\n\2", schedule)
    return schedule, classroom, weekday_str


def to_float(s, default=0.0):
    try:
        return float(s)
    except (TypeError, ValueError):
        return default

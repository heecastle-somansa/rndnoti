"""출처 공통 변환 도우미: 날짜·금액 읽기, 정정·재공고 판별."""
from __future__ import annotations

import re
from datetime import date

_DATE = re.compile(r"(\d{4})[-./년\s]*(\d{1,2})[-./월\s]*(\d{1,2})")
_REVISION_MARK = re.compile(r"[\[\(【<]\s*(정정|재공고|변경|재공모|연장)\s*[\]\)】>]|정정공고|재공고")


def parse_date(value: object) -> date | None:
    """'2026-11-09 10:00:00', '20261109', '2026.11.09', '2026년 11월 9일' 등을 날짜로."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{8}(\d{4,6})?", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    m = _DATE.search(text)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_amount(value: object) -> int | None:
    """'125000000', '125,000,000', '1.25E8', 0 → 원 단위 정수 (0이나 빈 값은 None)."""
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        amount = int(float(text))
    except ValueError:
        return None
    return amount if amount > 0 else None


def title_is_revision(title: str) -> bool:
    """제목에 [정정], [재공고] 등의 표기가 있는지."""
    return bool(_REVISION_MARK.search(title or ""))

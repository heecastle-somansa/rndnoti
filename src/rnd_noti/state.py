"""실행 상태 저장 (개발요청서 F1, F3, F13).

- state/last_run.json : 마지막으로 발송에 성공한 날짜
- state/seen.json     : 이미 보낸 공고 (uid → 처음 보낸 날 + 공고 내용)

공고 내용을 함께 저장하는 이유: 이미 보낸 공고 중 마감이 임박한 건을
다시 수집하지 않고도 리마인드로 보여주기 위해서다(F9).
이 파일들은 메일 발송이 성공한 뒤에만 갱신한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import Notice

STATE_DIR = Path(__file__).resolve().parents[2] / "state"
KST = timezone(timedelta(hours=9))

FIRST_RUN_DAYS = 7      # 최초 실행 시 수집 기간
SEEN_KEEP_DAYS = 90     # seen 보관 기간


def today_kst() -> date:
    return datetime.now(KST).date()


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


# ---------- last_run ----------

def load_last_run(path: Path | None = None) -> date | None:
    path = path or STATE_DIR / "last_run.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get("last_success_date")
    return date.fromisoformat(value) if value else None


def save_last_run(day: date, path: Path | None = None) -> None:
    _write_json(path or STATE_DIR / "last_run.json", {"last_success_date": day.isoformat()})


def collection_window(last_run: date | None, today: date) -> tuple[date, date]:
    """수집 기간 (시작일, 종료일). 양 끝 날짜를 포함한다.

    - 평소: 마지막 성공 실행일 다음 날 ~ 오늘 (건너뛴 주가 있어도 빠지지 않음)
    - 최초 실행: 7일 전 ~ 오늘
    - 같은 날 다시 실행: 오늘 ~ 오늘 (이미 보낸 공고는 seen으로 걸러짐)
    """
    if last_run is None:
        return today - timedelta(days=FIRST_RUN_DAYS), today
    return min(last_run + timedelta(days=1), today), today


# ---------- seen ----------

@dataclass
class SeenEntry:
    first_sent: date        # 처음 메일에 담아 보낸 날
    notice: Notice


def load_seen(path: Path | None = None) -> dict[str, SeenEntry]:
    path = path or STATE_DIR / "seen.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        uid: SeenEntry(
            first_sent=date.fromisoformat(item["first_sent"]),
            notice=Notice.from_dict(item["notice"]),
        )
        for uid, item in raw.items()
    }


def save_seen(seen: dict[str, SeenEntry], path: Path | None = None) -> None:
    data = {
        uid: {"first_sent": entry.first_sent.isoformat(), "notice": entry.notice.to_dict()}
        for uid, entry in seen.items()
    }
    _write_json(path or STATE_DIR / "seen.json", data)


def prune_seen(seen: dict[str, SeenEntry], today: date) -> dict[str, SeenEntry]:
    """처음 보낸 지 90일이 지난 항목을 지운다(F13). 파일이 끝없이 커지지 않게 하는 장치.

    단, 아직 마감 전인 공고는 남긴다(마감 임박 리마인드에 필요).
    """
    cutoff = today - timedelta(days=SEEN_KEEP_DAYS)
    return {uid: e for uid, e in seen.items()
            if e.first_sent >= cutoff or (e.notice.deadline and e.notice.deadline >= today)}

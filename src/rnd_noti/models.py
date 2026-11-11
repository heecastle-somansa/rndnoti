"""공통 공고 스키마 (개발요청서 3.3).

출처가 달라도 모든 공고는 Notice 하나의 형식으로 바뀐다.
출처에 없는 값은 빈 값("" 또는 None)으로 두고, 메일에서는 그 항목을 생략한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import date
from typing import Any

NOTICE_TYPE_MAIN = "본공고"
NOTICE_TYPE_PRE = "사전규격/수요조사"
NOTICE_TYPES = (NOTICE_TYPE_MAIN, NOTICE_TYPE_PRE)

_DATE_FIELDS = ("posted_at", "deadline")


@dataclass
class Notice:
    uid: str                              # 중복 판별 키 (나라장터: 공고번호, 그 외: 출처 + 공고 ID)
    source: str                           # 수집 출처 id (sources.yaml의 id)
    title: str                            # 공고명
    url: str = ""                         # 원문 링크 (절대 URL)
    source_name: str = ""                 # 수집 게시판명
    agency_group: str = ""                # 발주처 그룹 (정규화 단계에서 확정)
    track: str = ""                       # 공고 카테고리: R&D | 입찰 | 바우처 (정규화 단계에서 확정)
    ministry: str = ""                    # 소관 부처
    notice_type: str = NOTICE_TYPE_MAIN   # 본공고 | 사전규격/수요조사
    is_revision: bool = False             # 정정·재공고 여부
    revision_no: int = 0                  # 차수 (여러 차수 중 최신만 남길 때 사용)
    org: str = ""                         # 수요기관·공고기관
    budget_krw: int | None = None         # 사업금액(원)
    contract_info: str = ""               # 계약 방식·평가 방식
    posted_at: date | None = None         # 게시일
    deadline: date | None = None          # 마감일 (사전규격은 의견등록 마감일)
    matched: dict[str, list[str]] = field(default_factory=dict)  # {분류명: [매칭 키워드]}
    summary: str = ""                     # AI 요약 (2단계용 예약 필드, 1단계는 항상 빈 값)

    def __post_init__(self) -> None:
        if not self.uid:
            raise ValueError("Notice.uid는 비어 있을 수 없습니다")
        if self.notice_type not in NOTICE_TYPES:
            raise ValueError(f"알 수 없는 공고 유형: {self.notice_type!r}")

    def to_dict(self) -> dict[str, Any]:
        """JSON으로 저장할 수 있는 dict (날짜는 YYYY-MM-DD 문자열)."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in _DATE_FIELDS:
                value = value.isoformat() if value else None
            out[f.name] = value
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Notice":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        for name in _DATE_FIELDS:
            if kwargs.get(name):
                kwargs[name] = date.fromisoformat(kwargs[name])
        return cls(**kwargs)

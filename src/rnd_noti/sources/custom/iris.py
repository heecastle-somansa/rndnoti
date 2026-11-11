"""IRIS 통합 사업공고 전용 어댑터 (type: custom:iris).

IRIS 목록 화면은 자바스크립트로 데이터를 불러온다. 화면이 쓰는 목록 데이터 주소(JSON)를
같은 방식으로 호출한다. robots.txt에서 이 주소는 금지되어 있지 않다(2026-11-11 확인).

- '접수예정'과 '접수중' 탭을 모두 조회하고, 공고일(ancmDe)이 수집 기간 안인 것만 남긴다.
- 공고기관(전문기관, sorgnNm)을 org로 넣어 정규화 단계에서 발주처 그룹으로 재분류한다.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Mapping

from ...config import SourceConfig
from ...models import Notice
from ..base import SourceAdapter, SourceError
from ..common import parse_date, title_is_revision
from ..http import HttpClient
from ..registry import register

BASE = "https://www.iris.go.kr"
LIST_URL = BASE + "/contents/retrieveBsnsAncmBtinSituList.do"
VIEW_URL = BASE + "/contents/retrieveBsnsAncmView.do?ancmId={id}&ancmPrg={prg}"
TABS = ("ancmPre", "ancmIng")       # 접수예정, 접수중
MAX_PAGES = 20


@register("custom:iris")
class IrisAdapter(SourceAdapter):
    def __init__(self, cfg: SourceConfig, secrets: Mapping[str, str] | None = None,
                 http: HttpClient | None = None) -> None:
        super().__init__(cfg, secrets)
        self.http = http or HttpClient()

    def fetch(self, since: date, until: date) -> list[Notice]:
        self.http.get(self.cfg.url)                      # 세션 쿠키 받기
        found: dict[str, Notice] = {}
        for tab in TABS:
            page, total_pages = 1, 1
            while page <= min(total_pages, MAX_PAGES):
                resp = self.http.post(LIST_URL, {"pageIndex": page, "prgmId": "", "ancmPrg": tab},
                                      headers={"X-Requested-With": "XMLHttpRequest"})
                try:
                    data = json.loads(resp.text)
                except ValueError:
                    raise SourceError("IRIS 응답을 해석할 수 없습니다(화면 구조 변경 가능성)") from None
                total_pages = int((data.get("paginationInfo") or {}).get("totalPageCount") or 1)
                for item in data.get("listBsnsAncmBtinSitu") or []:
                    notice = self.to_notice(item, tab)
                    if notice and notice.posted_at and since <= notice.posted_at <= until:
                        found.setdefault(notice.uid, notice)
                page += 1
        return list(found.values())

    def to_notice(self, item: dict[str, Any], tab: str) -> Notice | None:
        aid, title = str(item.get("ancmId") or ""), str(item.get("ancmTl") or "").strip()
        if not aid or not title:
            return None
        return Notice(
            uid=f"iris:{aid}",
            source=self.cfg.id,
            source_name=self.cfg.name,
            title=title,
            url=VIEW_URL.format(id=aid, prg=tab),
            agency_group=self.cfg.group,
            ministry=str(item.get("blngGovdSeNm") or ""),
            notice_type=self.cfg.notice_type,
            is_revision=title_is_revision(title),
            org=str(item.get("sorgnNm") or "").strip(),
            contract_info=str(item.get("pbofrTpSeNmLst") or ""),
            posted_at=parse_date(item.get("ancmDe")),
            deadline=parse_date(item.get("rcveEndDe")),
        )

"""SBA(서울경제진흥원) 기업지원 전용 어댑터 (type: custom:sba).

SBA 목록은 화면이 내부 데이터 주소(Company_Support.aspx/GetData)를 호출해 그린다.
같은 주소를 호출해 JSON으로 받는다. robots.txt 없음(2026-11-11 확인).

- 게시일 필드가 없어 접수 시작일(new_dt_mig_sdate)을 게시일로 본다.
- 마감일(new_dt_mig_edate)은 "11.15"처럼 연도가 없어 시작일 연도로 채운다(시작일보다 앞이면 다음 해).
- options.menu_id : 기업지원 메뉴 ID (북마크 주소의 C= 값), options.max_pages : 조회할 페이지 수(1페이지 12건)
"""
from __future__ import annotations

import json
import re
from datetime import date
from typing import Any, Mapping

from ...config import SourceConfig
from ...models import Notice
from ..base import SourceAdapter, SourceError
from ..common import parse_date, title_is_revision
from ..http import HttpClient
from ..registry import register

DATA_URL = "https://www.sba.seoul.kr/Pages/ContentsMenu/Company_Support.aspx/GetData"
VIEW_URL = "https://www.sba.seoul.kr/Pages/ContentsMenu/Company_Support_Detail.aspx?RID={id}"
PAGE_SIZE = 12


def end_date(start: date | None, value: str) -> date | None:
    value = (value or "").strip()
    full = parse_date(value)
    if full or not start:
        return full
    m = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})", value)
    if not m:
        return None
    try:
        d = date(start.year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None
    return d if d >= start else date(start.year + 1, d.month, d.day)


@register("custom:sba")
class SbaAdapter(SourceAdapter):
    def __init__(self, cfg: SourceConfig, secrets: Mapping[str, str] | None = None,
                 http: HttpClient | None = None) -> None:
        super().__init__(cfg, secrets)
        self.http = http or HttpClient()

    def fetch(self, since: date, until: date) -> list[Notice]:
        menu_id = str(self.cfg.options.get("menu_id") or "")
        if not menu_id:
            raise SourceError("sources.yaml의 options.menu_id가 비어 있습니다")
        self.http.get(self.cfg.url)                       # 세션 쿠키 받기
        found: dict[str, Notice] = {}
        for page in range(1, int(self.cfg.options.get("max_pages", 3)) + 1):
            param = {"P_TYPE": "List", "P_PAGING": PAGE_SIZE, "P_PAGE_NUM": page, "P_MENU_ID": menu_id,
                     "P_ORDER": "", "P_KEYWORD": "", "P_SEARCH": "", "P_HASHTAG": "",
                     "P_RECORDID": "", "P_CONTACTID": ""}
            resp = self.http.post(DATA_URL, json={"param": param},
                                  headers={"Content-Type": "application/json; charset=utf-8"})
            try:
                body = json.loads(resp.text)["d"]
            except (ValueError, KeyError, TypeError):
                raise SourceError("SBA 응답을 해석할 수 없습니다(화면 구조 변경 가능성)") from None
            if not body.get("Result"):
                raise SourceError("SBA 목록 조회 실패")
            items = body.get("contents") or []
            for item in items:
                n = self.to_notice(item)
                if n and n.posted_at and since <= n.posted_at <= until:
                    found.setdefault(n.uid, n)
            if len(items) < PAGE_SIZE:
                break
        return list(found.values())

    def to_notice(self, item: dict[str, Any]) -> Notice | None:
        rid, title = str(item.get("recordId") or ""), str(item.get("new_name") or "").strip()
        if not rid or not title:
            return None
        start = parse_date(item.get("new_dt_mig_sdate"))
        return Notice(
            uid=f"sba:{rid}", source=self.cfg.id, source_name=self.cfg.name, title=title,
            url=VIEW_URL.format(id=rid), agency_group=self.cfg.group, ministry=self.cfg.ministry,
            notice_type=self.cfg.notice_type, is_revision=title_is_revision(title),
            org=str(item.get("new_txt_mig_org_nm") or "").strip(),
            posted_at=start, deadline=end_date(start, str(item.get("new_dt_mig_edate") or "")),
        )

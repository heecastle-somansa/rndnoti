"""범용 게시판 수집기 (type: generic_board).

sources.yaml의 설정(CSS 선택자)만으로 목록형 게시판을 수집한다. 코드 수정 없이 출처를 추가할 수 있다.

selectors (값 형식: "CSS선택자" 또는 "CSS선택자@속성", 속성만 "@onclick"이면 행 자체의 속성)
  row       : 공고 한 줄 (필수)
  title     : 공고명 (필수)
  link      : 상세 링크 (필수). 예) "td.subject a@href", "@onclick"
  date      : 게시일 (권장. 없으면 기간 필터 없이 모두 수집)
  deadline  : 마감일 (선택. 글자 안의 마지막 날짜를 마감일로 본다. "2026-11-01 ~ 2026-11-30" → 11-30)
  org       : 기관명 (선택)

options
  method        : get | post (기본 get)
  form          : post일 때 함께 보낼 값
  page_param    : 페이지 번호 이름 (예: pageIndex). 없으면 1페이지만
  page_start    : 첫 페이지 번호 (기본 1)
  max_pages     : 최대 페이지 수 (기본 3)
  sorted        : 최신순 정렬이면 true(기본). 기간보다 오래된 글만 나오면 다음 페이지를 보지 않는다
  link_regex    : link 값에서 뽑을 부분 (정규식 그룹). 예) "doBbsFView\\('(\\d+)','(\\d+)'"
  link_template : 뽑은 그룹으로 만들 상세 주소. 예) "https://.../View.do?cbIdx={1}&bcIdx={2}"
  id_params     : 상세 주소에서 공고 ID로 쓸 파라미터 이름 목록 (link_regex가 없을 때)
  title_remove  : 공고명에서 지울 글자 (정규식 목록). 예) ["\\(새 글\\)", "첨부파일 있음"]
  encoding      : 글자가 깨질 때만 지정 (예: euc-kr)
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from ..config import SourceConfig
from ..models import Notice
from .base import SourceAdapter, SourceError
from .common import parse_date, title_is_revision
from .http import HttpClient
from .registry import register

DEFAULT_MAX_PAGES = 3

# 공고 ID를 만들 때 버리는 파라미터 (페이지·검색 조건 등 매번 바뀌는 값)
_VOLATILE_PARAMS = {
    "page", "pageindex", "pagenumber", "pageno", "pagesize", "cpage", "curpage", "rows", "rowssel",
    "searchcondition", "searchkeyword", "searchtarget", "searchtext", "condition", "condition1",
    "keyword", "prekeywords", "sort", "orderby", "ordergb", "hashcode", "cat",
}
_JSESSION = re.compile(r";jsessionid=[^?#]*", re.I)
_DATE_ANY = re.compile(r"20\d{2}\s*[-./년]\s*\d{1,2}\s*[-./월]\s*\d{1,2}")


def _split_spec(spec: str) -> tuple[str, str | None]:
    """'td a@href' → ('td a', 'href'),  '@onclick' → ('', 'onclick'),  'td.date' → ('td.date', None)"""
    if "@" in spec:
        css, attr = spec.rsplit("@", 1)
        return css.strip(), attr.strip()
    return spec.strip(), None


def extract(row: Tag, spec: str | None) -> str:
    """행에서 선택자 값(텍스트 또는 속성)을 꺼낸다. 못 찾으면 빈 문자열."""
    if not spec:
        return ""
    css, attr = _split_spec(spec)
    el = row.select_one(css) if css else row
    if el is None:
        return ""
    if attr:
        value = el.get(attr, "")
        return " ".join(value) if isinstance(value, list) else str(value).strip()
    return " ".join(el.get_text(" ", strip=True).split())


def last_date(text: str) -> date | None:
    """글자 안의 마지막 날짜 (기간 표기의 끝 날짜)."""
    found = _DATE_ANY.findall(text or "")
    return parse_date(found[-1]) if found else None


def clean_url(url: str) -> str:
    return _JSESSION.sub("", url)


def notice_id_from_url(url: str, id_params: list[str] | None = None) -> str:
    """상세 주소에서 매번 바뀌는 값을 빼고 공고를 구별하는 ID를 만든다."""
    parts = urlsplit(clean_url(url))
    query = parse_qsl(parts.query, keep_blank_values=False)
    if id_params:
        wanted = {p.lower() for p in id_params}
        values = [v for k, v in query if k.lower() in wanted]
        if values:
            return "-".join(values)
    kept = sorted((k, v) for k, v in query if k.lower() not in _VOLATILE_PARAMS and v)
    q = urlencode(kept)
    return parts.path + (f"?{q}" if q else "")


@register("generic_board")
class GenericBoardAdapter(SourceAdapter):
    def __init__(self, cfg: SourceConfig, secrets: Mapping[str, str] | None = None,
                 http: HttpClient | None = None) -> None:
        super().__init__(cfg, secrets)
        self.http = http or HttpClient()
        self.opt: dict[str, Any] = dict(cfg.options)

    # ----- 페이지 가져오기 -----
    def _page_html(self, page: int | None) -> str:
        url, params, form = self.cfg.url, None, dict(self.opt.get("form") or {})
        param = self.opt.get("page_param")
        if param and page is not None:
            if self.opt.get("method", "get") == "post":
                form[param] = page
            else:
                params = {param: page}
        if self.opt.get("method", "get") == "post":
            resp = self.http.post(url, form)
        else:
            resp = self.http.get(url, params)
        if self.opt.get("encoding"):
            resp.encoding = self.opt["encoding"]
        elif not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text

    # ----- 행 → 공고 -----
    def _link(self, row: Tag) -> tuple[str, str]:
        """(상세 주소, 공고 ID)"""
        raw = extract(row, self.cfg.selectors.get("link"))
        regex, template = self.opt.get("link_regex"), self.opt.get("link_template")
        if regex:
            m = re.search(regex, raw)
            if not m:
                return "", ""
            groups = m.groups() or (m.group(0),)
            url = template.format(None, *groups) if template else urljoin(self.cfg.url, groups[0])
            return clean_url(url), "-".join(groups)
        if not raw or raw.startswith(("#", "javascript")):
            return "", ""
        url = clean_url(urljoin(self.cfg.url, raw))
        return url, notice_id_from_url(url, self.opt.get("id_params"))

    def parse_rows(self, html: str) -> list[Notice]:
        sel = self.cfg.selectors
        soup = BeautifulSoup(html, "html.parser")
        notices: list[Notice] = []
        for row in soup.select(sel["row"]):
            title = extract(row, sel.get("title"))
            for pattern in self.opt.get("title_remove") or []:
                title = re.sub(pattern, "", title)
            title = " ".join(title.split())
            url, nid = self._link(row)
            if not title or not nid:
                continue
            posted = parse_date(extract(row, sel.get("date"))) if sel.get("date") else None
            if sel.get("date") and posted is None:
                posted = last_date(extract(row, sel.get("date")))
            notices.append(Notice(
                uid=f"{self.cfg.id}:{nid}",
                source=self.cfg.id,
                source_name=self.cfg.name,
                title=title,
                url=url,
                agency_group=self.cfg.group,
                ministry=self.cfg.ministry,
                notice_type=self.cfg.notice_type,
                is_revision=title_is_revision(title),
                org=extract(row, sel.get("org")),
                posted_at=posted,
                deadline=last_date(extract(row, sel.get("deadline"))) if sel.get("deadline") else None,
            ))
        return notices

    # ----- 전체 흐름 -----
    def fetch(self, since: date, until: date) -> list[Notice]:
        if not self.cfg.url:
            raise SourceError("sources.yaml에 url이 비어 있습니다")
        for key in ("row", "title", "link"):
            if not self.cfg.selectors.get(key):
                raise SourceError(f"sources.yaml의 selectors.{key}가 비어 있습니다")
        has_paging = bool(self.opt.get("page_param"))
        start = int(self.opt.get("page_start", 1))
        max_pages = int(self.opt.get("max_pages", DEFAULT_MAX_PAGES)) if has_paging else 1
        sorted_desc = bool(self.opt.get("sorted", True))

        result: dict[str, Notice] = {}
        for i in range(max_pages):
            rows = self.parse_rows(self._page_html(start + i if has_paging else None))
            if i == 0 and not rows:
                raise SourceError("목록에서 공고를 찾지 못했습니다(화면 구조 변경 또는 접속 차단 가능성)")
            in_range = [n for n in rows if n.posted_at is None or since <= n.posted_at <= until]
            for n in in_range:
                result.setdefault(n.uid, n)
            # 최신순 목록이면 페이지 마지막 글(가장 오래된 글)이 기간보다 앞설 때 멈춘다.
            # (맨 위 고정 공지는 날짜가 오래돼도 영향을 주지 않는다)
            dated = [n for n in rows if n.posted_at]
            if not rows or (sorted_desc and dated and dated[-1].posted_at < since):
                break
        return list(result.values())

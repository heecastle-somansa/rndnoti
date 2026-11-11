"""과학기술정보통신부 사업공고 전용 어댑터 (type: custom:msit).

과기정통부 게시판은 빈 표를 먼저 그리고, 페이지 안의 스크립트가 제목·등록일을 채워 넣는다.
그래서 CSS 선택자로는 읽을 수 없고, 스크립트 속 값을 직접 읽는다.

  행 순서·글 번호 : onclick="fn_detail(3186915);" ... id="td_NO_0"
  제목            : sHtml+= unescape('제목');  →  $('#td_'+'NTT_SJ'+'_0').html(sHtml);
  등록일          : $('#td_'+'REG_DT'+'_0').html('Nov 10, 2026');   (주석 처리된 줄은 무시)
"""
from __future__ import annotations

import html as htmllib
import re
from datetime import date, datetime
from typing import Mapping

from ...config import SourceConfig
from ...models import Notice
from ..base import SourceAdapter, SourceError
from ..common import parse_date, title_is_revision
from ..http import HttpClient
from ..registry import register

VIEW_URL = "https://www.msit.go.kr/bbs/view.do?sCode=user&mId=311&mPid=121&bbsSeqNo=100&nttSeqNo={id}"
MAX_PAGES = 3

_ROW = re.compile(r"fn_detail\((\d+)\);\"[^>]*>\s*<div[^>]*id=\"td_NO_(\d+)\"")
_TITLE_PART = re.compile(r"sHtml\+=\s*unescape\('((?:[^'\\]|\\.)*)'\)")
_TITLE_SET = re.compile(r"\$\('#td_'\+'NTT_SJ'\+'_(\d+)'\)\.html\(sHtml\)")
_DATE_SET = re.compile(r"\$\('#td_'\+'REG_DT'\+'_(\d+)'\)\.html\('([^']+)'\)")


def _live_lines(text: str) -> str:
    """// 로 주석 처리된 줄을 뺀다."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("//"))


def _parse_reg_date(value: str) -> date | None:
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass
    return parse_date(value)


def parse_list(page_html: str) -> list[tuple[str, str, date | None]]:
    """(글 번호, 제목, 등록일) 목록."""
    text = _live_lines(page_html)
    ids = {int(idx): seq for seq, idx in _ROW.findall(text)}
    titles: dict[int, str] = {}
    last_part, pos = "", 0
    events = sorted([(m.start(), "part", m) for m in _TITLE_PART.finditer(text)]
                    + [(m.start(), "set", m) for m in _TITLE_SET.finditer(text)], key=lambda e: e[0])
    for _, kind, m in events:
        if kind == "part":
            last_part = m.group(1)
        else:
            titles[int(m.group(1))] = last_part
            last_part = ""
    dates = {int(i): _parse_reg_date(v) for i, v in _DATE_SET.findall(text) if v.strip()}
    rows = []
    for idx in sorted(ids):
        title = htmllib.unescape(re.sub(r"<[^>]+>", "", titles.get(idx, ""))).strip()
        if title:
            rows.append((ids[idx], " ".join(title.split()), dates.get(idx)))
    return rows


@register("custom:msit")
class MsitAdapter(SourceAdapter):
    def __init__(self, cfg: SourceConfig, secrets: Mapping[str, str] | None = None,
                 http: HttpClient | None = None) -> None:
        super().__init__(cfg, secrets)
        self.http = http or HttpClient()

    def fetch(self, since: date, until: date) -> list[Notice]:
        found: dict[str, Notice] = {}
        for page in range(1, MAX_PAGES + 1):
            rows = parse_list(self.http.get(self.cfg.url, {"pageIndex": page}).text)
            if page == 1 and not rows:
                raise SourceError("목록에서 공고를 찾지 못했습니다(화면 구조 변경 가능성)")
            for seq, title, posted in rows:
                if posted and not (since <= posted <= until):
                    continue
                found.setdefault(seq, Notice(
                    uid=f"msit:{seq}", source=self.cfg.id, source_name=self.cfg.name, title=title,
                    url=VIEW_URL.format(id=seq), agency_group=self.cfg.group, ministry=self.cfg.ministry,
                    notice_type=self.cfg.notice_type, is_revision=title_is_revision(title),
                    org=self.cfg.ministry, posted_at=posted))
            dated = [r[2] for r in rows if r[2]]
            if not rows or (dated and dated[-1] < since):
                break
        return list(found.values())

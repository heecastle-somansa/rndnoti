"""리포트: 구독자별 메일 내용 구성과 렌더링 (개발요청서 5장).

용어는 명세서 표기를 그대로 쓴다: 신규 공고, 사전규격/수요조사, 마감 임박, 분류/키워드, 11월 2주차.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import GroupConfig
from .match import KIND_REMINDER, KIND_REVISION, REMIND_DAYS, Item
from .models import NOTICE_TYPE_PRE
from .normalize import format_amount

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"
CARDS_PER_SECTION = 5

# 색 (D-day 배지: D-3 이내 빨강, D-4~D-7 주황, 그 외 회색. 의견 D-n은 항상 주황)
RED, ORANGE, GRAY = "#d92d20", "#e8590c", "#6b7280"
_LEADING_REVISION = re.compile(r"^\s*[\[\(【]\s*정정\s*[\]\)】]\s*")


def week_label(d: date) -> str:
    """'11월 2주차'. 월요일 시작 주이며, 그 주의 목요일이 속한 달의 몇 번째 주인지로 센다.

    예) 2026-11-09(월)~15(일) → 목요일 11-12 → 11월 2주차
    """
    thursday = d - timedelta(days=d.weekday()) + timedelta(days=3)
    return f"{thursday.month}월 {(thursday.day - 1) // 7 + 1}주차"


@dataclass
class Card:
    title: str
    url: str
    badges: list[tuple[str, str]]       # (글자, 배경색)
    dday_text: str
    dday_color: str
    meta: str                           # 2행: 기관 · 금액 · 계약/공고 유형
    summary: str                        # 3행: AI 요약 (1단계는 항상 빈 값 → 줄 자체를 출력하지 않음)
    keywords: str                       # 4행: 매칭 키워드
    org: str
    amount: str
    sort_key: tuple


@dataclass
class Section:
    name: str
    cards: list[Card]
    rest: list[Card]
    track_label: str = ""               # 공고 카테고리 이름 (예: 부처별 R&D 공고)
    track_count: int = 0                # 그 카테고리의 전체 공고 수
    first_in_track: bool = False        # 카테고리의 첫 섹션이면 그 위에 카테고리 띠를 그린다

    @property
    def count(self) -> int:
        return len(self.cards) + len(self.rest)


@dataclass
class Report:
    subject: str
    week: str
    heading: str
    categories: str
    new_main: int
    new_pre: int
    urgent: int
    total_new: int
    sections: list[Section]
    period: str
    failed_sources: list[str] = field(default_factory=list)


def dday(item: Item, today: date) -> tuple[str, str, int | None]:
    n = item.notice
    if n.deadline is None:
        return "마감 미정", GRAY, None
    days = (n.deadline - today).days
    if n.notice_type == NOTICE_TYPE_PRE:
        return f"의견 D-{days}", ORANGE, days
    color = RED if days <= 3 else ORANGE if days <= REMIND_DAYS else GRAY
    return f"D-{days}", color, days


def make_card(item: Item, today: date) -> Card:
    n = item.notice
    text, color, days = dday(item, today)
    badges = []
    if item.kind == KIND_REVISION or (n.is_revision and item.kind != KIND_REMINDER):
        badges.append(("정정", "#2f6fdf"))
    if item.kind == KIND_REMINDER:
        badges.append(("리마인드", "#7a5af8"))
    amount = format_amount(n.budget_krw)
    meta = " · ".join(x for x in (n.org, amount, n.contract_info or n.notice_type) if x)
    words: list[str] = []
    for kws in n.matched.values():
        words += [w for w in kws if w not in words]
    title = n.title
    if any(b[0] == "정정" for b in badges):     # 배지와 겹치는 제목 앞 [정정] 표기는 뺀다
        title = _LEADING_REVISION.sub("", title) or n.title
    return Card(
        title=title, url=n.url, badges=badges, dday_text=text, dday_color=color, meta=meta,
        summary=n.summary, keywords=", ".join(words), org=n.org, amount=amount,
        sort_key=(days is None, days if days is not None else 0, n.title),
    )


def _section_order(groups: GroupConfig) -> tuple[list[str], dict[str, str]]:
    if groups.group_by == "ministry":
        order: list[str] = []
        for g in groups.groups:
            if g.ministry and g.ministry not in order:
                order.append(g.ministry)
        return order, {g.name: g.ministry for g in groups.groups}
    return groups.names, {g.name: g.name for g in groups.groups}


def build_report(items: list[Item], categories: list[str], groups: GroupConfig, today: date,
                 since: date, until: date, failed_sources: list[str]) -> Report:
    order, to_section = _section_order(groups)
    fallback = to_section.get(groups.fallback_group, groups.fallback_group)
    # 큰 구획 = 공고 카테고리(R&D / 입찰 / 바우처), 그 안에서 발주처 그룹별 섹션
    tracks = [(t.name, t.label) for t in groups.tracks] or [("", "")]
    first_track = tracks[0][0]
    buckets: dict[tuple[str, str], list[Card]] = {}
    for item in items:
        key = (item.notice.ministry or fallback) if groups.group_by == "ministry" \
            else to_section.get(item.notice.agency_group, fallback)
        track = item.notice.track if item.notice.track in dict(tracks) else first_track
        buckets.setdefault((track, key), []).append(make_card(item, today))
    sections = []
    for track, label in tracks:
        names = [k for t, k in buckets if t == track]
        total = sum(len(buckets[(track, k)]) for k in names)
        first = True
        for name in order + [k for k in names if k not in order]:
            cards = sorted(buckets.get((track, name), []), key=lambda c: c.sort_key)
            if cards:
                sections.append(Section(name, cards[:CARDS_PER_SECTION], cards[CARDS_PER_SECTION:],
                                        track_label=label, track_count=total, first_in_track=first))
                first = False

    fresh = [i for i in items if i.kind != KIND_REMINDER]
    new_pre = sum(1 for i in fresh if i.notice.notice_type == NOTICE_TYPE_PRE)
    new_main = len(fresh) - new_pre
    urgent = sum(1 for i in items if (d := dday(i, today)[2]) is not None and 0 <= d <= REMIND_DAYS)
    week = week_label(today)
    if fresh:
        subject = f"[R&D Noti] {week} — 관련 공고 {len(fresh)}건, 마감 임박 {urgent}건"
        heading = f"이번 주 관련 공고 {len(fresh)}건, 마감 임박 {urgent}건"
    else:
        subject = f"[R&D Noti] {week} — 신규 공고 없음"
        heading = "이번 주 신규 공고 없음" + (f", 마감 임박 {urgent}건" if urgent else "")
    return Report(
        subject=subject, week=week, heading=heading, categories=", ".join(categories),
        new_main=new_main, new_pre=new_pre, urgent=urgent, total_new=len(fresh), sections=sections,
        period=f"{since:%Y.%m.%d} ~ {until:%Y.%m.%d}", failed_sources=failed_sources,
    )


_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)),
                   autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)


def render(report: Report) -> tuple[str, str]:
    """(HTML 본문, 텍스트 본문)"""
    ctx = {"r": report, "RED": RED, "GRAY": GRAY}
    return (_env.get_template("weekly_report.html").render(**ctx),
            _env.get_template("weekly_report.txt").render(**ctx))


def render_named(template: str, **ctx) -> str:
    return _env.get_template(template).render(**ctx)

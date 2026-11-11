"""매칭: 중복·정정 처리, 제외어, 분류 매칭, 구독자별 선택 (개발요청서 F3~F7, F9, F14, F15).

흐름
  1) 모든 출처 공고 → dedupe_same_uid (F4: 같은 공고의 여러 차수 중 최신만)
  2) drop_expired (마감일이 지난 공고 제외)
  3) dedupe_cross_source (F14: 기관 게시판 > 통합포털)
  4) 구독자마다 select_for_subscriber → 신규/정정 + 마감 임박 리마인드
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta

from .config import KeywordConfig
from .models import Notice
from .normalize import norm, title_key
from .state import SeenEntry

KIND_NEW = "new"            # 이번 주 처음 보내는 공고
KIND_REVISION = "revision"  # 이미 보낸 공고의 정정·재공고 → '정정' 배지
KIND_REMINDER = "reminder"  # 이미 보낸 공고 중 마감 임박 → '리마인드' 배지

REMIND_DAYS = 7             # F9: D-0 ~ D-7


@dataclass
class Subscriber:
    name: str
    email: str
    categories: list[str]
    active: bool = True
    sources: list[str] = field(default_factory=list)          # 비어 있으면 전체
    exclude_sources: list[str] = field(default_factory=list)

    def wants_source(self, source_id: str) -> bool:
        if self.sources and source_id not in self.sources:
            return False
        return source_id not in self.exclude_sources


@dataclass
class Item:
    """메일에 들어갈 공고 한 건 (구독자별 매칭 결과 포함)."""
    notice: Notice
    kind: str = KIND_NEW


# ---------- 1~3) 전체 공고 정리 ----------

def dedupe_same_uid(notices: list[Notice]) -> list[Notice]:
    """같은 uid가 여러 번 나오면 차수가 가장 높은(같으면 나중에 게시된) 것만 남긴다."""
    best: dict[str, Notice] = {}
    for n in notices:
        cur = best.get(n.uid)
        if cur is None or (n.revision_no, n.posted_at or date.min) > (cur.revision_no, cur.posted_at or date.min):
            best[n.uid] = n
    return list(best.values())


def drop_expired(notices: list[Notice], today: date) -> list[Notice]:
    return [n for n in notices if n.deadline is None or n.deadline >= today]


def dedupe_cross_source(notices: list[Notice], portal_sources: set[str]) -> list[Notice]:
    """F14: 공고명(공백·괄호 제거) + 기관(발주처 그룹) + 마감일이 같으면 하나만 남긴다.

    - 기관은 출처마다 표기가 달라(IRIS '정보통신기획평가원' vs IITP 게시판) 정규화된 발주처 그룹으로 비교한다.
    - 한쪽에 마감일이 없으면 공고명+기관만으로 같은 공고로 본다.
    - 기관 게시판 공고를 통합포털 공고보다 우선한다.
    """
    ordered = sorted(notices, key=lambda n: n.source in portal_sources)   # 기관 게시판 먼저
    kept: list[Notice] = []
    index: dict[tuple[str, str], list[Notice]] = {}
    for n in ordered:
        key = (title_key(n.title), n.agency_group)
        same = index.setdefault(key, [])
        if any(k.deadline is None or n.deadline is None or k.deadline == n.deadline for k in same):
            continue
        same.append(n)
        kept.append(n)
    return kept


# ---------- 4) 키워드 매칭 ----------

def contains_any(title_norm: str, words: tuple[str, ...]) -> list[str]:
    return [w for w in words if norm(w) and norm(w) in title_norm]


def match_categories(title: str, chosen: list[str], kw: KeywordConfig) -> dict[str, list[str]]:
    """F6·F7: 공통 제외어 → 전체 제외. 분류별 제외어 → 그 분류만 제외. 키워드 하나라도 있으면 매칭."""
    t = norm(title)
    if contains_any(t, kw.exclude):
        return {}
    matched: dict[str, list[str]] = {}
    for name in chosen:
        cat = kw.categories.get(name)
        if cat is None or contains_any(t, cat.exclude):
            continue
        hits = contains_any(t, cat.keywords)
        if hits:
            matched[name] = hits
    return matched


def classify_against_seen(n: Notice, seen: dict[str, SeenEntry]) -> str | None:
    """F3·F4: 처음 보는 공고면 new, 이미 보낸 공고의 새 차수/정정이면 revision, 그대로면 None(제외)."""
    entry = seen.get(n.uid)
    if entry is None:
        return KIND_NEW
    old = entry.notice
    if n.revision_no > old.revision_no or (n.is_revision and not old.is_revision):
        return KIND_REVISION
    return None


def select_for_subscriber(
    pool: list[Notice], seen: dict[str, SeenEntry], sub: Subscriber, kw: KeywordConfig, today: date,
) -> list[Item]:
    items: list[Item] = []
    taken: set[str] = set()
    for n in pool:
        if not sub.wants_source(n.source):
            continue
        kind = classify_against_seen(n, seen)
        if kind is None:
            continue
        matched = match_categories(n.title, sub.categories, kw)
        if matched:
            items.append(Item(replace(n, matched=matched), kind))
            taken.add(n.uid)
    # F9: 이미 보낸 공고 중 마감 임박 건은 리마인드로 다시 포함
    limit = today + timedelta(days=REMIND_DAYS)
    for uid, entry in seen.items():
        n = entry.notice
        if uid in taken or not n.deadline or not (today <= n.deadline <= limit):
            continue
        if not sub.wants_source(n.source):
            continue
        matched = match_categories(n.title, sub.categories, kw)
        if matched:
            items.append(Item(replace(n, matched=matched), KIND_REMINDER))
    return items


def update_seen(seen: dict[str, SeenEntry], sent: list[Item], today: date) -> dict[str, SeenEntry]:
    """발송한 신규·정정 공고를 seen에 기록한다. 정정은 처음 보낸 날을 유지하고 내용만 갱신."""
    out = dict(seen)
    for item in sent:
        if item.kind == KIND_REMINDER:
            continue
        n = replace(item.notice, matched={})
        prev = out.get(n.uid)
        out[n.uid] = SeenEntry(first_sent=prev.first_sent if prev else today, notice=n)
    return out

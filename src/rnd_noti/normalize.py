"""정규화: 출처별로 모은 공고를 메일에 쓸 수 있게 다듬는다 (개발요청서 2장 2번, 3.2, F5, F8).

- 제목 필터(sources.yaml의 title_filter)
- 공고 유형 자동 전환(제목에 '수요조사'·'사전규격'이 있으면 사전규격/수요조사)
- 발주처 그룹 배정(통합포털 공고는 공고기관명으로 재분류)
- 비교용 문자열 정규화, 금액 표기
"""
from __future__ import annotations

import re
from dataclasses import replace

from .config import GroupConfig, SourceConfig
from .models import NOTICE_TYPE_PRE, Notice

_PRE_WORDS = ("수요조사", "사전규격")
_BRACKETS = re.compile(r"[()\[\]{}<>「」『』【】〈〉《》“”\"'‘’]")


def norm(text: str) -> str:
    """F5: 공백 제거 + 영문 소문자화. "망 분리" == "망분리", "VDI" == "vdi"."""
    return re.sub(r"\s+", "", text or "").lower()


def title_key(title: str) -> str:
    """F14: 출처 간 중복 비교용 공고명 (공백·괄호 제거)."""
    return norm(_BRACKETS.sub("", title or ""))


def passes_title_filter(title: str, words: tuple[str, ...]) -> bool:
    if not words:
        return True
    t = norm(title)
    return any(norm(w) in t for w in words)


def assign_group(org: str, groups: GroupConfig) -> str:
    """공고기관명 → 발주처 그룹. 별칭(aliases)이 기관명에 들어 있으면 그 그룹, 없으면 기타 기관."""
    o = norm(org)
    if o:
        for g in groups.groups:
            if any(norm(a) and norm(a) in o for a in g.aliases):
                return g.name
    return groups.fallback_group


def assign_track(title: str, source: SourceConfig, groups: GroupConfig) -> str:
    """공고 카테고리 정하기. 기본은 출처의 category, 공고명에 다른 카테고리의 단어가 있으면 그쪽으로 옮긴다.

    예) 기업마당(기본 바우처)의 "○○ 기술개발사업 공고" → R&D
        NIPA 사업공고(기본 R&D)의 "AI 바우처 지원사업 수요기업 모집" → 바우처
    fixed 카테고리(입찰)의 출처는 옮기지 않는다.
    """
    if not groups.tracks:
        return ""
    default = source.track or groups.tracks[0].name
    current = groups.track(default)
    if current is not None and current.fixed:
        return default
    t = norm(title)
    for track in groups.tracks:
        if any(norm(w) and norm(w) in t for w in track.keywords):
            return track.name
    return default


def normalize(notice: Notice, source: SourceConfig, groups: GroupConfig) -> Notice:
    changes: dict = {"track": assign_track(notice.title, source, groups)}
    if any(w in notice.title for w in _PRE_WORDS):
        changes["notice_type"] = NOTICE_TYPE_PRE
    if source.group == groups.portal_group:
        group = assign_group(notice.org, groups)
    else:
        group = source.group
    changes["agency_group"] = group
    group_cfg = next((g for g in groups.groups if g.name == group), None)
    if not notice.ministry and group_cfg:
        changes["ministry"] = group_cfg.ministry
    if not notice.org and source.group != groups.portal_group:
        changes["org"] = source.group           # 기관 게시판은 기관명을 비워두지 않는다
    changes["summary"] = ""                     # 2단계 전까지 항상 빈 값
    return replace(notice, **changes)


def format_amount(krw: int | None) -> str:
    """1억 이상 '12.5억', 1억 미만 '8,500만', 없으면 빈 값."""
    if not krw:
        return ""
    if krw >= 100_000_000:
        return f"{krw / 100_000_000:.1f}억"
    man = krw // 10_000
    return f"{man:,}만" if man else f"{krw:,}원"

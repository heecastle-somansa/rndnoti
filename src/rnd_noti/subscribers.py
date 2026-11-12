"""구독자 목록 읽기 (Secrets의 SUBSCRIBERS_JSON, 개발요청서 7.1).

오류 메시지와 로그에는 메일 주소를 절대 넣지 않고 구독자 name만 쓴다.
"""
from __future__ import annotations

import json
import re

from .config import GroupConfig, KeywordConfig
from .match import REGIONS, Subscriber

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SubscriberError(Exception):
    pass


def load_subscribers(raw: str, kw: KeywordConfig, source_ids: set[str],
                     groups: GroupConfig | None = None) -> tuple[list[Subscriber], list[str]]:
    """(활성 구독자 목록, 경고 메시지 목록). 형식이 틀리면 SubscriberError."""
    if not raw or not raw.strip():
        raise SubscriberError("SUBSCRIBERS_JSON이 비어 있습니다")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SubscriberError(f"SUBSCRIBERS_JSON 형식 오류(쉼표·따옴표·대괄호 확인): {exc.msg} 위치 {exc.pos}") from None
    if not isinstance(data, list):
        raise SubscriberError("SUBSCRIBERS_JSON은 [ ... ] 목록이어야 합니다")
    subs: list[Subscriber] = []
    warnings: list[str] = []
    names: set[str] = set()
    for i, item in enumerate(data, 1):
        if not isinstance(item, dict):
            raise SubscriberError(f"{i}번째 구독자 형식이 잘못됐습니다")
        name = str(item.get("name") or "").strip()
        if not name:
            raise SubscriberError(f"{i}번째 구독자에 name이 없습니다")
        if name in names:
            raise SubscriberError(f"구독자 name이 중복됩니다: {name}")
        names.add(name)
        email = str(item.get("email") or "").strip()
        if not _EMAIL.match(email):
            raise SubscriberError(f"구독자 '{name}'의 email 형식이 잘못됐습니다")
        cats = [str(c) for c in item.get("categories") or []]
        unknown = [c for c in cats if c not in kw.categories]
        if unknown:
            warnings.append(f"구독자 '{name}': keywords.yaml에 없는 분류는 무시합니다: {', '.join(unknown)}")
        cats = [c for c in cats if c in kw.categories]
        if not cats:
            warnings.append(f"구독자 '{name}': 고른 분류가 없어 발송하지 않습니다")
        for key in ("sources", "exclude_sources"):
            bad = [s for s in item.get(key) or [] if s not in source_ids]
            if bad:
                warnings.append(f"구독자 '{name}': {key}에 없는 출처 id: {', '.join(bad)}")
        tracks = [str(t) for t in item.get("source_categories") or []]
        known = groups.track_names if groups else []
        bad = [t for t in tracks if t not in known]
        if bad:
            warnings.append(f"구독자 '{name}': source_categories에 없는 카테고리는 무시합니다: {', '.join(bad)} "
                            f"(가능: {' | '.join(known)})")
        tracks = [t for t in tracks if t in known]
        regions = [str(r).strip() for r in item.get("regions") or [] if str(r).strip()]
        bad = [r for r in regions if not any(r.startswith(x) for x in REGIONS)]
        if bad:
            warnings.append(f"구독자 '{name}': regions에 알 수 없는 지역: {', '.join(bad)} (예: 서울, 경기)")
        sub = Subscriber(
            name=name, email=email, categories=cats,
            active=bool(item.get("active", True)),
            sources=[str(s) for s in item.get("sources") or []],
            exclude_sources=[str(s) for s in item.get("exclude_sources") or []],
            tracks=tracks, regions=regions,
        )
        if sub.active and cats:
            subs.append(sub)
    return subs, warnings

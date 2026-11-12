"""설정 파일(config/*.yaml) 읽기와 형식 검사.

설정을 잘못 고쳤을 때 어느 파일의 무엇이 틀렸는지 한국어로 알려준다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import NOTICE_TYPE_MAIN, NOTICE_TYPES

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

_TYPE_PATTERN = re.compile(r"^(api_g2b_bid|api_g2b_prespec|generic_board|custom:[a-z0-9_]+)$")


class ConfigError(Exception):
    """설정 파일 내용이 잘못됐을 때."""


# ---------- 분류·키워드 ----------

@dataclass(frozen=True)
class Category:
    name: str
    keywords: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    only_in: tuple[str, ...] = ()         # 이 공고 카테고리에서만 쓰는 분류 (비면 모든 카테고리)
    require: tuple[str, ...] = ()         # 이 단어 중 하나도 공고명에 있어야 매칭 (비면 조건 없음)
    regional: bool = True                 # false면 제목 앞에 [지역]이 붙은 지역 공고에는 적용하지 않음


@dataclass(frozen=True)
class KeywordConfig:
    exclude: tuple[str, ...]              # 공통 제외 키워드
    categories: dict[str, Category]


# ---------- 발주처 그룹 ----------

@dataclass(frozen=True)
class Group:
    name: str
    ministry: str = ""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Track:
    """공고 카테고리 (예: R&D / 입찰 / 바우처). 메일의 가장 큰 구획."""
    name: str
    label: str = ""                       # 메일에 보이는 이름
    keywords: tuple[str, ...] = ()        # 공고명에 이 단어가 있으면 이 카테고리로 옮긴다
    fixed: bool = False                   # true면 이 카테고리 출처의 공고는 다른 카테고리로 옮기지 않는다
    dedupe_by_title: bool = False         # true면 기관이 달라도 공고명이 같으면 같은 공고로 본다(입찰)


@dataclass(frozen=True)
class GroupConfig:
    group_by: str                         # agency | ministry
    portal_group: str
    fallback_group: str
    groups: tuple[Group, ...]             # 순서 = 메일 섹션 순서
    tracks: tuple[Track, ...] = ()        # 공고 카테고리. 순서 = 메일의 큰 구획 순서 (없으면 구획 없이 표시)

    @property
    def track_names(self) -> list[str]:
        return [t.name for t in self.tracks]

    def track(self, name: str) -> Track | None:
        return next((t for t in self.tracks if t.name == name), None)

    @property
    def names(self) -> list[str]:
        return [g.name for g in self.groups]


# ---------- 출처 ----------

@dataclass(frozen=True)
class SourceConfig:
    id: str
    name: str
    group: str
    type: str
    track: str = ""                                         # 공고 카테고리 (sources.yaml의 category)
    ministry: str = ""
    enabled: bool = True
    url: str = ""
    notice_type: str = NOTICE_TYPE_MAIN
    title_filter: tuple[str, ...] = ()
    selectors: dict[str, str] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)   # 어댑터별 추가 설정
    note: str = ""                                          # 사람이 읽는 메모
    manual_check: bool = False                              # 꺼 둔 출처를 메일의 "직접 확인할 출처"에 링크로 싣는다


@dataclass(frozen=True)
class AppConfig:
    keywords: KeywordConfig
    groups: GroupConfig
    sources: tuple[SourceConfig, ...]

    @property
    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]

    @property
    def manual_sources(self) -> list[tuple[str, str]]:
        """자동 수집을 꺼 두었지만 사람이 직접 확인할 출처의 (이름, 주소)."""
        return [(s.name, s.url) for s in self.sources if not s.enabled and s.manual_check and s.url]


# ---------- 읽기 ----------

def _read_yaml(path: Path) -> Any:
    if not path.exists():
        raise ConfigError(f"설정 파일이 없습니다: {path.name}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} 형식 오류(들여쓰기·쉼표·따옴표를 확인하세요): {exc}") from exc


def _str_list(value: Any, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{where}: 목록([a, b, c]) 형식이어야 합니다")
    return tuple(str(v).strip() for v in value if str(v).strip())


def _bool(value, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: true 또는 false여야 합니다")
    return value


def load_keywords(path: Path | None = None) -> KeywordConfig:
    path = path or CONFIG_DIR / "keywords.yaml"
    data = _read_yaml(path) or {}
    raw = data.get("categories")
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"{path.name}: categories에 분류가 하나 이상 있어야 합니다")
    categories: dict[str, Category] = {}
    for name, body in raw.items():
        body = body or {}
        keywords = _str_list(body.get("keywords"), f"{path.name} > {name} > keywords")
        if not keywords:
            raise ConfigError(f"{path.name}: 분류 '{name}'에 keywords가 없습니다")
        categories[str(name)] = Category(
            name=str(name),
            keywords=keywords,
            exclude=_str_list(body.get("exclude"), f"{path.name} > {name} > exclude"),
            only_in=_str_list(body.get("only_in"), f"{path.name} > {name} > only_in"),
            require=_str_list(body.get("require"), f"{path.name} > {name} > require"),
            regional=_bool(body.get("regional", True), f"{path.name} > {name} > regional"),
        )
    return KeywordConfig(
        exclude=_str_list(data.get("exclude"), f"{path.name} > exclude"),
        categories=categories,
    )


def load_groups(path: Path | None = None) -> GroupConfig:
    path = path or CONFIG_DIR / "groups.yaml"
    data = _read_yaml(path) or {}
    group_by = data.get("group_by", "agency")
    if group_by not in ("agency", "ministry"):
        raise ConfigError(f"{path.name}: group_by는 agency 또는 ministry여야 합니다 (현재: {group_by})")
    raw = data.get("groups")
    if not isinstance(raw, list) or not raw:
        raise ConfigError(f"{path.name}: groups에 그룹이 하나 이상 있어야 합니다")
    groups: list[Group] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("name"):
            raise ConfigError(f"{path.name}: 각 그룹에는 name이 있어야 합니다")
        groups.append(Group(
            name=str(item["name"]),
            ministry=str(item.get("ministry") or ""),
            aliases=_str_list(item.get("aliases"), f"{path.name} > {item['name']} > aliases"),
        ))
    names = [g.name for g in groups]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise ConfigError(f"{path.name}: 그룹 이름이 중복됩니다: {', '.join(sorted(dup))}")
    fallback = str(data.get("fallback_group", "기타 기관"))
    if fallback not in names:
        raise ConfigError(f"{path.name}: fallback_group '{fallback}'이 groups 목록에 없습니다")
    tracks: list[Track] = []
    for item in data.get("categories") or []:
        if not isinstance(item, dict) or not item.get("name"):
            raise ConfigError(f"{path.name}: categories의 각 항목에는 name이 있어야 합니다")
        where = f"{path.name} > categories > {item['name']}"
        unknown = set(item) - {"name", "label", "title_keywords", "fixed", "dedupe_by_title"}
        if unknown:
            raise ConfigError(f"{where}: 알 수 없는 항목입니다(오타 확인): {', '.join(sorted(unknown))}")
        tracks.append(Track(
            name=str(item["name"]), label=str(item.get("label") or item["name"]),
            keywords=_str_list(item.get("title_keywords"), f"{where} > title_keywords"),
            fixed=bool(item.get("fixed", False)), dedupe_by_title=bool(item.get("dedupe_by_title", False)),
        ))
    tnames = [t.name for t in tracks]
    if len(set(tnames)) != len(tnames):
        raise ConfigError(f"{path.name}: categories의 name이 중복됩니다")
    return GroupConfig(
        group_by=group_by,
        portal_group=str(data.get("portal_group", "통합포털")),
        fallback_group=fallback,
        groups=tuple(groups),
        tracks=tuple(tracks),
    )


_SOURCE_KEYS = {"id", "name", "group", "category", "ministry", "enabled", "type", "url",
                "notice_type", "title_filter", "selectors", "options", "note", "manual_check"}


def load_sources(path: Path | None = None, groups: GroupConfig | None = None) -> tuple[SourceConfig, ...]:
    path = path or CONFIG_DIR / "sources.yaml"
    data = _read_yaml(path)
    if not isinstance(data, list) or not data:
        raise ConfigError(f"{path.name}: 출처가 하나 이상 있어야 합니다")
    sources: list[SourceConfig] = []
    seen_ids: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            raise ConfigError(f"{path.name}: 각 출처는 'id: ...' 형식의 항목이어야 합니다")
        sid = str(item.get("id") or "")
        where = f"{path.name} > {sid or '(id 없음)'}"
        for key in ("id", "name", "group", "type"):
            if not item.get(key):
                raise ConfigError(f"{where}: '{key}' 항목이 필요합니다")
        unknown = set(item) - _SOURCE_KEYS
        if unknown:
            raise ConfigError(f"{where}: 알 수 없는 항목입니다(오타 확인): {', '.join(sorted(unknown))}")
        if sid in seen_ids:
            raise ConfigError(f"{where}: id가 중복됩니다")
        seen_ids.add(sid)
        stype = str(item["type"])
        if not _TYPE_PATTERN.match(stype):
            raise ConfigError(
                f"{where}: type '{stype}'을 알 수 없습니다 "
                "(api_g2b_bid | api_g2b_prespec | generic_board | custom:이름)")
        notice_type = str(item.get("notice_type") or NOTICE_TYPE_MAIN)
        if notice_type not in NOTICE_TYPES:
            raise ConfigError(f"{where}: notice_type은 {' 또는 '.join(NOTICE_TYPES)}여야 합니다")
        group = str(item["group"])
        if groups and group != groups.portal_group and group not in groups.names:
            raise ConfigError(f"{where}: group '{group}'이 groups.yaml에 없습니다")
        track = str(item.get("category") or "")
        if groups and groups.tracks:
            track = track or groups.tracks[0].name
            if track not in groups.track_names:
                raise ConfigError(f"{where}: category '{track}'이 groups.yaml의 categories에 없습니다 "
                                  f"(가능: {' | '.join(groups.track_names)})")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"{where}: enabled는 true 또는 false여야 합니다")
        manual_check = item.get("manual_check", False)
        if not isinstance(manual_check, bool):
            raise ConfigError(f"{where}: manual_check는 true 또는 false여야 합니다")
        selectors = item.get("selectors") or {}
        if not isinstance(selectors, dict):
            raise ConfigError(f"{where}: selectors 형식이 잘못됐습니다")
        sources.append(SourceConfig(
            id=sid, name=str(item["name"]), group=group, type=stype, track=track,
            ministry=str(item.get("ministry") or ""),
            enabled=enabled,
            url=str(item.get("url") or ""),
            notice_type=notice_type,
            title_filter=_str_list(item.get("title_filter"), f"{where} > title_filter"),
            selectors={str(k): str(v) for k, v in selectors.items()},
            options=dict(item.get("options") or {}),
            note=str(item.get("note") or ""),
            manual_check=manual_check,
        ))
    return tuple(sources)


def load_config(config_dir: Path | None = None) -> AppConfig:
    config_dir = config_dir or CONFIG_DIR
    groups = load_groups(config_dir / "groups.yaml")
    keywords = load_keywords(config_dir / "keywords.yaml")
    for cat in keywords.categories.values():
        bad = [t for t in cat.only_in if t not in groups.track_names]
        if bad:
            raise ConfigError(f"keywords.yaml > {cat.name} > only_in: groups.yaml의 categories에 없는 이름입니다: "
                              f"{', '.join(bad)} (가능: {' | '.join(groups.track_names)})")
    return AppConfig(
        keywords=keywords,
        groups=groups,
        sources=load_sources(config_dir / "sources.yaml", groups),
    )

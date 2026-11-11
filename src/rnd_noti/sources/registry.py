"""sources.yaml의 type → 어댑터 연결.

- api_g2b_bid, api_g2b_prespec, generic_board : 기본 제공 어댑터
- custom:<이름> : sources/custom/<이름>.py 에 있는 전용 어댑터

새 전용 어댑터는 파일 하나를 추가하고 @register("custom:<이름>")만 붙이면 된다.
"""
from __future__ import annotations

import importlib
from typing import Callable, Mapping

from ..config import SourceConfig
from .base import SourceAdapter, SourceError

_REGISTRY: dict[str, type[SourceAdapter]] = {}

# 기본 제공 어댑터가 들어 있는 모듈 (해당 단계에서 파일이 추가됨)
_BUILTIN_MODULES = {
    "api_g2b_bid": "g2b_bid",
    "api_g2b_prespec": "g2b_prespec",
    "generic_board": "generic_board",
}


def register(type_name: str) -> Callable[[type[SourceAdapter]], type[SourceAdapter]]:
    def decorator(cls: type[SourceAdapter]) -> type[SourceAdapter]:
        _REGISTRY[type_name] = cls
        return cls
    return decorator


def _module_for(type_name: str) -> str | None:
    if type_name in _BUILTIN_MODULES:
        return f"{__package__}.{_BUILTIN_MODULES[type_name]}"
    if type_name.startswith("custom:"):
        return f"{__package__}.custom.{type_name.split(':', 1)[1]}"
    return None


def get_adapter_class(type_name: str) -> type[SourceAdapter]:
    if type_name not in _REGISTRY:
        module = _module_for(type_name)
        if module:
            try:
                importlib.import_module(module)
            except ModuleNotFoundError as exc:
                if exc.name != module:
                    raise
    if type_name not in _REGISTRY:
        raise SourceError(f"수집 방식 '{type_name}'에 해당하는 어댑터가 없습니다")
    return _REGISTRY[type_name]


def create_adapter(cfg: SourceConfig, secrets: Mapping[str, str] | None = None) -> SourceAdapter:
    return get_adapter_class(cfg.type)(cfg, secrets)

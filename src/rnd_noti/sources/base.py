"""수집 어댑터 공통 인터페이스.

모든 출처는 fetch(since, until) 하나만 구현하면 된다.
실패하면 SourceError를 던지고, 실행기는 그 출처만 '실패'로 기록한 뒤 계속 진행한다(F12).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Mapping

from ..config import SourceConfig
from ..models import Notice


class SourceError(Exception):
    """한 출처의 수집 실패. 메시지는 메일 하단·로그에 그대로 표시되므로
    인증키나 메일 주소 같은 비밀값을 넣지 않는다."""


class SourceAdapter(ABC):
    def __init__(self, cfg: SourceConfig, secrets: Mapping[str, str] | None = None) -> None:
        self.cfg = cfg
        self.secrets = secrets or {}

    @abstractmethod
    def fetch(self, since: date, until: date) -> list[Notice]:
        """since~until(양 끝 포함)에 게시된 공고를 공통 형식으로 돌려준다."""

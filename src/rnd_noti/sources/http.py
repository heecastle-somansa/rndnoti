"""모든 어댑터가 함께 쓰는 HTTP 호출 도구 (개발요청서 F2).

- 호출 사이 0.5초 간격
- 실패 시 3회까지 재시도
- 오류 메시지에서 인증키 등 비밀값을 '***'로 가린다 (메일·로그에 그대로 나가므로)
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Iterable

import requests

from .base import SourceError

USER_AGENT = "Mozilla/5.0 (compatible; RnDNoti/1.0; +https://github.com/)"
_KEY_IN_URL = re.compile(r"(?i)(serviceKey|apiKey|key|token)=[^&\s]+")


def mask(text: str, secrets: Iterable[str] = ()) -> str:
    """문자열에서 비밀값과 URL 속 키 파라미터를 가린다."""
    for s in secrets:
        if s:
            text = text.replace(s, "***")
            text = text.replace(requests.utils.quote(s, safe=""), "***")
    return _KEY_IN_URL.sub(r"\1=***", text)


class HttpClient:
    def __init__(
        self,
        *,
        delay: float = 0.5,
        retries: int = 3,
        timeout: float = 30.0,
        secrets: Iterable[str] = (),
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.secrets = [s for s in secrets if s]
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        self._sleep = sleep
        self._called = False

    def get(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        last_error = ""
        for attempt in range(1, self.retries + 1):
            if self._called:
                self._sleep(self.delay * (attempt if attempt > 1 else 1))
            self._called = True
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code >= 500 or resp.status_code == 429:
                    last_error = f"서버 응답 {resp.status_code}"
                    continue
                if resp.status_code >= 400:
                    raise SourceError(f"서버 응답 {resp.status_code} (접속 거부 또는 주소 오류)")
                return resp
            except requests.RequestException as exc:
                last_error = mask(f"{type(exc).__name__}: {exc}", self.secrets)
        raise SourceError(f"{self.retries}회 시도 모두 실패 ({last_error})")

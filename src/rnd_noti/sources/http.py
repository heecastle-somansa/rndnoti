"""모든 어댑터가 함께 쓰는 HTTP 호출 도구 (개발요청서 F2).

- 호출 사이 0.5초 간격
- 실패 시 3회까지 재시도
- 오류 메시지에서 인증키 등 비밀값을 '***'로 가린다 (메일·로그에 그대로 나가므로)
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit
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


def _place(url: str) -> tuple[str, str]:
    """주소에서 (도메인, 경로)만 뽑는다. http/https, www 차이는 무시."""
    u = urlsplit(url)
    host = (u.hostname or "").removeprefix("www.")
    return host, u.path.rstrip("/")


def moved_to(requested: str, resp: Any) -> str | None:
    """요청한 주소가 다른 곳으로 '이사'했으면 새 주소를 돌려준다(없으면 None).

    도메인이 바뀌었거나, 사이트가 '영구 이동(301·308)'이라고 알려 준 경우만 본다.
    (로그인 화면으로 잠깐 보내는 것 같은 임시 이동은 무시)
    """
    history = getattr(resp, "history", None) or []
    final = getattr(resp, "url", "") or ""
    if not history or not final or _place(requested) == _place(final):
        return None
    permanent = any(getattr(h, "status_code", 0) in (301, 308) for h in history)
    if permanent or _place(requested)[0] != _place(final)[0]:
        return final.split("?")[0]
    return None


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
        self.moved: dict[str, str] = {}      # 요청 주소 → 옮겨 간 새 주소

    def get(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        return self._request("get", url, params=params)

    def post(self, url: str, data: dict[str, Any] | None = None, *,
             json: Any = None, headers: dict[str, str] | None = None) -> requests.Response:
        return self._request("post", url, data=data, json=json, headers=headers)

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        last_error = ""
        for attempt in range(1, self.retries + 1):
            if self._called:
                self._sleep(self.delay * (attempt if attempt > 1 else 1))
            self._called = True
            try:
                resp = getattr(self.session, method)(url, timeout=self.timeout, **kwargs)
                if resp.status_code >= 500 or resp.status_code == 429:
                    last_error = f"서버 응답 {resp.status_code}"
                    continue
                if resp.status_code >= 400:
                    raise SourceError(f"서버 응답 {resp.status_code} (접속 거부 또는 주소 오류)")
                new = moved_to(url, resp)
                if new:
                    self.moved[url.split("?")[0]] = new
                return resp
            except requests.RequestException as exc:
                last_error = mask(f"{type(exc).__name__}: {exc}", self.secrets)
        raise SourceError(f"{self.retries}회 시도 모두 실패 ({last_error})")

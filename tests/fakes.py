"""테스트용 가짜 HTTP (실제 사이트에 접속하지 않는다)."""
from pathlib import Path

from rnd_noti.sources.http import HttpClient

FIX = Path(__file__).parent / "fixtures"
# 테스트는 운영 설정(config/)이 아니라 고정 사본을 쓴다 → 키워드·출처를 고쳐도 테스트가 깨지지 않는다
TEST_CONFIG = Path(__file__).parents[1] / "samples" / "config"


class FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status
        self.encoding, self.apparent_encoding = "utf-8", "utf-8"


class FakeSession:
    """호출 기록을 남기고 responder(method, url, kwargs)가 돌려준 응답을 준다."""

    def __init__(self, responder):
        self.responder, self.calls, self.headers = responder, [], {}

    def _call(self, method, url, **kw):
        self.calls.append((method, url, kw))
        result = self.responder(method, url, kw)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, FakeResp) else FakeResp(result)

    def get(self, url, **kw):
        return self._call("get", url, **kw)

    def post(self, url, **kw):
        return self._call("post", url, **kw)


def fake_http(responder):
    session = FakeSession(responder)
    return HttpClient(session=session, sleep=lambda s: None), session


def fixture_text(name):
    return (FIX / name).read_text(encoding="utf-8")

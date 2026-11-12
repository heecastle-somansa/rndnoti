"""2단계 테스트: 나라장터 입찰공고·사전규격 수집 (샘플 응답 사용, 실제 호출 없음)."""
from datetime import date
from pathlib import Path

import pytest
import requests

from rnd_noti.config import SourceConfig
from rnd_noti.models import NOTICE_TYPE_MAIN, NOTICE_TYPE_PRE
from rnd_noti.sources import registry
from rnd_noti.sources.base import SourceError
from rnd_noti.sources.common import parse_amount, parse_date, title_is_revision
from rnd_noti.sources.g2b_base import parse_response
from rnd_noti.sources.http import HttpClient, mask

FIX = Path(__file__).parent / "fixtures"
KEY = "TEST+KEY/abc=="


class FakeResp:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class FakeSession:
    """호출 기록을 남기고, 정해둔 응답을 돌려주는 가짜 세션."""

    def __init__(self, responder):
        self.responder, self.calls, self.headers = responder, [], {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        result = self.responder(url, params or {})
        if isinstance(result, Exception):
            raise result
        return result


def make(source_type, responder, options=None, secrets=None):
    cfg = SourceConfig(
        id="g2b_bid" if source_type == "api_g2b_bid" else "g2b_prespec",
        name="나라장터", group="나라장터", ministry="나라장터", type=source_type,
        url="https://www.g2b.go.kr/",
        notice_type=NOTICE_TYPE_MAIN if source_type == "api_g2b_bid" else NOTICE_TYPE_PRE,
        options=options or {"operations": ["용역"], "page_size": 100})
    session, sleeps = FakeSession(responder), []
    secrets = {"DATA_GO_KR_KEY": KEY} if secrets is None else secrets
    http = HttpClient(secrets=[KEY], session=session, sleep=sleeps.append)
    adapter = registry.get_adapter_class(source_type)(cfg, secrets, http=http)
    return adapter, session, sleeps


def fixture(name):
    return FakeResp((FIX / name).read_text(encoding="utf-8"))


# ---------- 입찰공고 ----------

def test_bid_all_pages_and_mapping():
    def responder(url, params):
        return fixture(f"g2b_bid_page{params['pageNo']}.json")

    adapter, session, sleeps = make("api_g2b_bid", responder)
    notices = adapter.fetch(date(2026, 11, 9), date(2026, 11, 11))

    assert [c[1]["pageNo"] for c in session.calls] == [1, 2]          # 모든 페이지 순회
    assert session.calls[0][0].endswith("/BidPublicInfoService02/getBidPblancListInfoServc")
    p = session.calls[0][1]
    assert (p["inqryBgnDt"], p["inqryEndDt"], p["serviceKey"]) == ("202611090000", "202611112359", KEY)
    assert sleeps == [0.5]                                             # 호출 간 0.5초

    assert [n.uid for n in notices] == ["g2b:R26BK00000101", "g2b:R26BK00000102", "g2b:R26BK00000104"]
    a, b, c = notices
    assert a.title == "2027년 업무망 논리적 망분리 고도화 사업"
    assert (a.org, a.budget_krw, a.contract_info) == ("○○공단", 1_250_000_000, "협상에 의한 계약")
    assert (a.posted_at, a.deadline) == (date(2026, 11, 9), date(2026, 11, 20))
    assert a.is_revision is False and a.notice_type == "본공고" and a.agency_group == "나라장터"
    assert a.url.startswith("https://www.g2b.go.kr/")
    assert a.summary == ""
    assert b.is_revision is True and b.revision_no == 1
    assert b.budget_krw == 85_000_000                                  # 배정예산 없으면 추정가격
    assert c.budget_krw is None and c.deadline is None and c.contract_info == ""


def test_bid_operations_and_period_split():
    adapter, session, _ = make(
        "api_g2b_bid",
        lambda u, p: FakeResp('{"response":{"header":{"resultCode":"00"},"body":{"totalCount":0,"items":""}}}'),
        options={"operations": ["용역", "물품"]})
    adapter.fetch(date(2026, 10, 20), date(2026, 11, 9))              # 21일 → 7일씩 3구간
    ops = [c[0].rsplit("/", 1)[1] for c in session.calls]
    assert ops == ["getBidPblancListInfoServc"] * 3 + ["getBidPblancListInfoThng"] * 3
    ranges = [(c[1]["inqryBgnDt"][:8], c[1]["inqryEndDt"][:8]) for c in session.calls[:3]]
    assert ranges == [("20261020", "20261026"), ("20261027", "20261102"), ("20261103", "20261109")]


def test_bad_operation_name():
    adapter, _, _ = make("api_g2b_bid", lambda u, p: None, options={"operations": ["용역", "용엮"]})
    with pytest.raises(SourceError, match="용엮"):
        adapter.fetch(date(2026, 11, 9), date(2026, 11, 9))


def test_base_url_override_from_config():
    adapter, session, _ = make(
        "api_g2b_bid", lambda u, p: FakeResp('{"response":{"header":{"resultCode":"00"},"body":{"items":[]}}}'),
        options={"operations": ["용역"], "base_url": "https://apis.data.go.kr/new/Path/"})
    adapter.fetch(date(2026, 11, 9), date(2026, 11, 9))
    assert session.calls[0][0] == "https://apis.data.go.kr/new/Path/getBidPblancListInfoServc"


# ---------- 사전규격 ----------

def test_prespec_mapping():
    adapter, session, _ = make("api_g2b_prespec", lambda u, p: fixture("g2b_prespec.json"))
    notices = adapter.fetch(date(2026, 11, 9), date(2026, 11, 11))
    assert session.calls[0][0].endswith("/HrcspSsstndrdInfoService/getPublicPrcureThngInfoServc")
    assert len(session.calls) == 1                                     # totalCount 2 → 1페이지로 끝
    a, b = notices
    assert a.uid == "g2b_pre:1112223" and a.notice_type == "사전규격/수요조사"
    assert (a.org, a.budget_krw, a.deadline) == ("○○연구원", 450_000_000, date(2026, 11, 16))
    assert a.url == "https://www.g2b.go.kr/"                           # 상세 링크 없으면 목록 주소
    assert b.is_revision is True and b.org == "◇◇군청" and b.deadline is None


# ---------- 오류 처리 ----------

def test_missing_key():
    adapter, session, _ = make("api_g2b_bid", lambda u, p: None, secrets={})
    with pytest.raises(SourceError, match="DATA_GO_KR_KEY"):
        adapter.fetch(date(2026, 11, 9), date(2026, 11, 9))
    assert session.calls == []


def test_unregistered_key_xml_error():
    adapter, _, _ = make("api_g2b_bid", lambda u, p: fixture("g2b_error_key.xml"))
    with pytest.raises(SourceError, match="인증키가 등록되지 않았거나"):
        adapter.fetch(date(2026, 11, 9), date(2026, 11, 9))


def test_json_error_header():
    with pytest.raises(SourceError, match="공공데이터포털 오류"):
        parse_response('{"response":{"header":{"resultCode":"22","resultMsg":"LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"}}}')


def test_retry_three_times_then_fail_without_leaking_key():
    def responder(url, params):
        return requests.ConnectionError(f"failed {url}?serviceKey={requests.utils.quote(KEY, safe='')}&pageNo=1")

    adapter, session, sleeps = make("api_g2b_bid", responder)
    with pytest.raises(SourceError) as exc:
        adapter.fetch(date(2026, 11, 9), date(2026, 11, 9))
    assert len(session.calls) == 3                                     # 3회 재시도
    msg = str(exc.value)
    assert "3회 시도 모두 실패" in msg
    assert KEY not in msg and "TEST" not in msg and "serviceKey=***" in msg


def test_retry_recovers_after_server_error():
    answers = [FakeResp("", 503), fixture("g2b_prespec.json")]
    adapter, session, sleeps = make("api_g2b_prespec", lambda u, p: answers.pop(0))
    assert len(adapter.fetch(date(2026, 11, 9), date(2026, 11, 9))) == 2
    assert len(session.calls) == 2


def test_http_4xx_is_not_retried():
    adapter, session, _ = make("api_g2b_bid", lambda u, p: FakeResp("forbidden", 403))
    with pytest.raises(SourceError, match="403"):
        adapter.fetch(date(2026, 11, 9), date(2026, 11, 9))
    assert len(session.calls) == 1


# ---------- 공통 도우미 ----------

@pytest.mark.parametrize("value, expected", [
    ("2026-11-09 10:00:00", date(2026, 11, 9)), ("20261109", date(2026, 11, 9)),
    ("202611091030", date(2026, 11, 9)), ("2026.11.9", date(2026, 11, 9)),
    ("2026년 11월 9일", date(2026, 11, 9)), ("", None), (None, None), ("미정", None),
])
def test_parse_date(value, expected):
    assert parse_date(value) == expected


def test_parse_amount_and_revision():
    assert parse_amount("1,250,000,000") == 1_250_000_000
    assert parse_amount("1.25E8") == 125_000_000
    assert parse_amount("0") is None and parse_amount("") is None and parse_amount("abc") is None
    assert title_is_revision("[정정] A사업") and title_is_revision("(재공고) B")
    assert not title_is_revision("정보보호 고도화")


def test_mask():
    assert mask("x?serviceKey=abc%2B&y=1") == "x?serviceKey=***&y=1"
    assert mask("token SECRET here", ["SECRET"]) == "token *** here"


def test_403_explains_missing_api_approval():
    from datetime import date as _d
    from rnd_noti.config import SourceConfig
    from rnd_noti.sources.base import SourceError
    from rnd_noti.sources.g2b_bid import G2BBidAdapter
    from tests.fakes import FakeResp, fake_http
    http, _ = fake_http(lambda m, u, kw: FakeResp("Forbidden", 403))
    cfg = SourceConfig(id="g2b_bid", name="나라장터 입찰공고", group="나라장터", type="api_g2b_bid")
    with pytest.raises(SourceError, match="입찰공고정보서비스.*승인"):
        G2BBidAdapter(cfg, {"DATA_GO_KR_KEY": "KEY123"}, http=http).fetch(_d(2026, 11, 2), _d(2026, 11, 9))

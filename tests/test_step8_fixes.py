"""운영 점검 후 보완: 403 안내, 호출 수 절약, 직접 확인할 출처."""
from datetime import date

import pytest

from rnd_noti.config import SourceConfig, load_config
from rnd_noti.main import API_CHECK_DAYS, CHECK_DAYS, collect
from rnd_noti.report import build_report, render
from rnd_noti.sources.base import SourceError
from rnd_noti.sources.g2b_bid import G2BBidAdapter
from tests.fakes import TEST_CONFIG, FakeResp, fake_http

CFG = load_config(TEST_CONFIG)
TODAY = date(2026, 11, 12)
BID = SourceConfig(id="g2b_bid", name="나라장터 입찰공고", group="나라장터", type="api_g2b_bid",
                   options={"operations": ["용역"]})
EMPTY = '{"response": {"header": {"resultCode": "00"}, "body": {"items": [], "totalCount": 0}}}'


def test_403_shows_called_address_and_portal_message_without_key():
    http, _ = fake_http(lambda m, u, kw: FakeResp("Forbidden", 403))
    with pytest.raises(SourceError) as exc:
        G2BBidAdapter(BID, {"DATA_GO_KR_KEY": "KEY123"}, http=http).fetch(TODAY, TODAY)
    msg = str(exc.value)
    assert "/1230000/ad/BidPublicInfoService02/getBidPblancListInfoServc" in msg
    assert "End Point" in msg and "KEY123" not in msg


def test_page_size_default_500_and_capped_at_999():
    seen = []
    http, _ = fake_http(lambda m, u, kw: (seen.append(kw["params"]["numOfRows"]), FakeResp(EMPTY))[1])
    adapter = G2BBidAdapter(BID, {"DATA_GO_KR_KEY": "K"}, http=http)
    adapter.fetch(TODAY, TODAY)
    assert seen == [500] and http.calls == 1
    big = SourceConfig(id="g2b_bid", name="x", group="나라장터", type="api_g2b_bid", options={"page_size": 5000})
    assert G2BBidAdapter(big, {"DATA_GO_KR_KEY": "K"}, http=http).page_size == 999


def test_check_mode_uses_short_window_for_api_sources():
    asked = {}

    class Fake:
        def __init__(self, src): self.src = src
        def fetch(self, since, until):
            asked[self.src.id] = (until - since).days
            return []

    from datetime import timedelta
    days_of = lambda s: API_CHECK_DAYS if s.type.startswith("api_") else CHECK_DAYS
    collect(CFG, {}, TODAY, TODAY, adapter_factory=lambda s, e: Fake(s),
            since_for=lambda s: TODAY - timedelta(days=days_of(s)))
    assert asked["g2b_bid"] == 7 and asked["g2b_prespec"] == 7 and asked["iris"] == 30


@pytest.mark.liveconfig
def test_manual_sources_come_from_disabled_sources_marked_manual_check():
    names = [n for n, _ in load_config().manual_sources]
    assert len(names) == 3 and all(any(k in n for k in ("GPU", "ETRI", "SBA")) for n in names)
    assert not any("IITP" in n or "NTIS" in n for n in names)


def test_manual_sources_shown_in_mail_only_when_present():
    manual = [("SBA", "https://www.sba.seoul.kr/x")]
    r = build_report([], ["보안 일반"], CFG.groups, TODAY, TODAY, TODAY, [], manual)
    html, text = render(r)
    assert "직접 확인할 출처" in html and 'href="https://www.sba.seoul.kr/x"' in html
    assert "직접 확인할 출처" in text and "https://www.sba.seoul.kr/x" in text
    html2, text2 = render(build_report([], ["보안 일반"], CFG.groups, TODAY, TODAY, TODAY, []))
    assert "직접 확인할 출처" not in html2 and "직접 확인할 출처" not in text2


def test_operation_names_can_be_overridden_and_404_explains():
    urls = []
    http, _ = fake_http(lambda m, u, kw: (urls.append(u), FakeResp("API not found", 404))[1])
    cfg = SourceConfig(id="g2b_bid", name="x", group="나라장터", type="api_g2b_bid",
                       options={"operations": ["용역"], "operation_names": {"용역": "newOp"}})
    with pytest.raises(SourceError, match="operation_names"):
        G2BBidAdapter(cfg, {"DATA_GO_KR_KEY": "K"}, http=http).fetch(TODAY, TODAY)
    assert urls[0].endswith("/BidPublicInfoService02/newOp")


def test_changed_response_fields_are_reported_not_silently_empty():
    body = '{"response": {"header": {"resultCode": "00"}, "body": {"items": [{"newNo": "1", "newNm": "a"}], "totalCount": 1}}}'
    http, _ = fake_http(lambda m, u, kw: FakeResp(body))
    with pytest.raises(SourceError, match="newNo"):
        G2BBidAdapter(BID, {"DATA_GO_KR_KEY": "K"}, http=http).fetch(TODAY, TODAY)

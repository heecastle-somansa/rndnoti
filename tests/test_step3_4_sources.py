"""3·4단계 테스트: 범용 게시판 수집기와 전용 어댑터.

fixtures/board_*.html, iris_list.json, sba_list.json은 2026-11-11 실제 화면에서 잘라낸 샘플이다
(개인 정보 부분은 제거). 실제 사이트에는 접속하지 않는다.
"""
import json
from datetime import date

import pytest

from rnd_noti.config import SourceConfig, load_config
from rnd_noti.sources.base import SourceError
from rnd_noti.sources.custom.msit import parse_list
from rnd_noti.sources.custom.sba import end_date
from rnd_noti.sources.generic_board import extract, last_date, notice_id_from_url
from rnd_noti.sources.registry import get_adapter_class
from tests.fakes import TEST_CONFIG, FakeResp, fake_http, fixture_text

SOURCES = {s.id: s for s in load_config(TEST_CONFIG).sources}


def adapter_for(sid, responder):
    http, session = fake_http(responder)
    cfg = SOURCES[sid]
    return get_adapter_class(cfg.type)(cfg, {}, http=http), session


def board(sid):
    return adapter_for(sid, lambda m, u, kw: fixture_text(f"board_{sid}.html"))[0]


# ---------- 실제 sources.yaml 설정 + 실제 화면 샘플 ----------

EXPECTED = {
    # sid: (첫 공고 uid, 첫 공고 게시일, 첫 공고 마감일, 행 수, 상세주소 시작)
    "nia": ("nia:78336-30181-30181", date(2026, 11, 11), None, 4,
            "https://www.nia.or.kr/site/nia_kor/ex/bbs/View.do?cbIdx=78336&bcIdx=30181"),
    "mss": ("mss:310-1072312-1072312", date(2026, 11, 5), None, 3,
            "https://www.mss.go.kr/site/smba/ex/bbs/View.do?cbIdx=310&bcIdx=1072312"),
    "keit_rnd": ("keit_rnd:I24578-2026", date(2026, 10, 23), date(2026, 11, 20), 3,
                 "https://srome.keit.re.kr/srome/biz/perform/opnnPrpsl/retrieveTaskAnncmInfoView.do?prgmId=XPG201040000&ancmId=I24578&bsnsYy=2026"),
    "nipa": ("nipa:/home/2-2/16982", date(2026, 10, 30), date(2026, 11, 30), 3, "https://www.nipa.kr/home/2-2/16982"),
    "kisa": ("kisa:3781", date(2026, 10, 13), None, 4, "https://www.kisa.or.kr/401/form?postSeq=3781"),
    "export_infra": ("export_infra:13047", date(2026, 10, 23), None, 3,
                     "https://www.exportvoucher.com/portal/board/boardView?bbs_id=1&ntt_id=13047"),
    "smtech": ("smtech:S02847-11", date(2026, 10, 1), date(2026, 11, 30), 3,
               "https://www.smtech.go.kr/front/ifg/no/notice02_detail.do?"),
    "bizinfo": ("bizinfo:PBLN_000000000127825", date(2026, 11, 10), date(2026, 11, 16), 3,
                "https://www.bizinfo.go.kr/sii/siia/selectSIIA200Detail.do?"),
}


@pytest.mark.parametrize("sid", list(EXPECTED))
def test_real_board_samples(sid):
    uid, posted, deadline, count, url_start = EXPECTED[sid]
    rows = board(sid).parse_rows(fixture_text(f"board_{sid}.html"))
    assert len(rows) == count
    first = rows[0]
    assert (first.uid, first.posted_at, first.deadline) == (uid, posted, deadline)
    assert first.url.startswith(url_start)
    assert ";jsessionid" not in first.url
    assert first.title and "새 글" not in first.title and "첨부파일" not in first.title
    assert first.source == sid and first.agency_group == SOURCES[sid].group


def test_bizinfo_org_and_keit_demand_type():
    assert board("bizinfo").parse_rows(fixture_text("board_bizinfo.html"))[0].org == "충청남도"
    assert SOURCES["keit_demand"].notice_type == "사전규격/수요조사"


# ---------- 기간 필터와 페이지 순회 ----------

def test_period_filter_and_stop_when_older():
    adapter, session = adapter_for("nia", lambda m, u, kw: fixture_text("board_nia.html"))
    got = adapter.fetch(date(2026, 11, 11), date(2026, 11, 11))
    assert [n.uid for n in got] == ["nia:78336-30181-30181"]
    assert len(session.calls) == 1              # 1페이지 마지막 글(11-10)이 기간보다 앞서 멈춤
    assert session.calls[0][2]["params"] == {"pageIndex": 1}
    adapter, session = adapter_for("nia", lambda m, u, kw: fixture_text("board_nia.html"))
    adapter.fetch(date(2026, 11, 10), date(2026, 11, 11))
    assert len(session.calls) == 3              # 마지막 글도 기간 안 → max_pages(3)까지


def test_unsorted_board_reads_all_pages_with_post():
    adapter, session = adapter_for("smtech", lambda m, u, kw: fixture_text("board_smtech.html"))
    adapter.fetch(date(2026, 11, 1), date(2026, 11, 11))
    assert len(session.calls) == 5              # sorted: false → max_pages 5 모두
    assert all(c[0] == "post" for c in session.calls)
    assert session.calls[2][2]["data"] == {"pageIndex": 3}


def test_empty_first_page_is_failure():
    adapter, _ = adapter_for("kisa", lambda m, u, kw: "<html><body>점검 중</body></html>")
    with pytest.raises(SourceError, match="공고를 찾지 못했습니다"):
        adapter.fetch(date(2026, 11, 1), date(2026, 11, 11))


def test_missing_selectors_or_url():
    cfg = SourceConfig(id="x", name="X", group="IITP", type="generic_board", url="https://a.kr/")
    http, _ = fake_http(lambda m, u, kw: "")
    with pytest.raises(SourceError, match="selectors.row"):
        get_adapter_class("generic_board")(cfg, {}, http=http).fetch(date(2026, 11, 1), date(2026, 11, 2))


# ---------- 도우미 ----------

def test_helpers():
    assert last_date("2026. 11. 01 ~ 2026. 11. 30") == date(2026, 11, 30)
    assert last_date("신청기간 : 2026-10-30 12:00 ~ 2026-11-30 15:00") == date(2026, 11, 30)
    assert last_date("상시") is None
    assert notice_id_from_url("https://a.kr/view.do;jsessionid=AB?no=5&page=3&searchKeyword=x") == "/view.do?no=5"
    assert notice_id_from_url("https://a.kr/v?seq=9&pageSize=20", ["seq"]) == "9"


def test_extract_row_attribute():
    from bs4 import BeautifulSoup
    row = BeautifulSoup('<tr onclick="go(1)"><td><a href="/x">T</a></td></tr>', "html.parser").tr
    assert extract(row, "@onclick") == "go(1)"
    assert extract(row, "td a@href") == "/x"
    assert extract(row, "td a") == "T"
    assert extract(row, "td.none") == ""


# ---------- 전용 어댑터 ----------

def test_iris_adapter():
    def responder(method, url, kw):
        if method == "get":
            return "<html></html>"
        return fixture_text("iris_list.json")

    adapter, session = adapter_for("iris", responder)
    got = adapter.fetch(date(2026, 11, 1), date(2026, 11, 11))
    posts = [c for c in session.calls if c[0] == "post"]
    assert [p[2]["data"]["ancmPrg"] for p in posts] == ["ancmPre", "ancmIng"]
    first = json.loads(fixture_text("iris_list.json"))["listBsnsAncmBtinSitu"][0]
    n = next(x for x in got if x.uid == f"iris:{first['ancmId']}")
    assert n.title == first["ancmTl"] and n.org == first["sorgnNm"] and n.ministry == first["blngGovdSeNm"]
    assert n.deadline == date(2026, 11, 30) and n.agency_group == "통합포털"
    assert n.url.startswith("https://www.iris.go.kr/contents/retrieveBsnsAncmView.do?ancmId=")


def test_msit_parser_reads_script_values():
    rows = parse_list(fixture_text("msit_list.html"))
    assert rows == [
        ("3186915", "2027년 정보보호 R&D 신규과제 공고", date(2026, 11, 10)),
        ("3186914", "[정정] 클라우드 네이티브 전환 지원사업 공고", date(2026, 11, 6)),
        ("3186800", "오래된 공고", date(2026, 9, 1)),
    ]


def test_msit_adapter_period_and_revision():
    adapter, session = adapter_for("msit", lambda m, u, kw: fixture_text("msit_list.html"))
    got = adapter.fetch(date(2026, 11, 3), date(2026, 11, 11))
    assert [n.uid for n in got] == ["msit:3186915", "msit:3186914"]
    assert got[1].is_revision and "nttSeqNo=3186914" in got[1].url
    assert len(session.calls) == 1              # 기간보다 오래된 글이 나와 1페이지에서 멈춤


def test_sba_adapter():
    def responder(method, url, kw):
        return "<html></html>" if method == "get" else fixture_text("sba_list.json")

    adapter, session = adapter_for("sba", responder)
    got = adapter.fetch(date(2026, 10, 25), date(2026, 11, 11))
    assert {n.title for n in got} == {"(수정)2026년 건설분야 혁신기술 실증 지원사업", "2027년 스마트 제조혁신 지원사업 통합공고"}
    n = next(x for x in got if "건설" in x.title)
    assert (n.posted_at, n.deadline, n.org) == (date(2026, 11, 3), date(2026, 11, 15), "서울연구원")
    post = next(c for c in session.calls if c[0] == "post")
    assert post[2]["json"]["param"]["P_MENU_ID"] == "8D010DF4-6777-EC11-80E8-9418827691E2"


def test_sba_end_date_year():
    assert end_date(date(2026, 11, 3), "11.15") == date(2026, 11, 15)
    assert end_date(date(2026, 12, 20), "01.10") == date(2027, 1, 10)
    assert end_date(date(2026, 12, 20), "상시") is None


# ---------- 21곳 설정 상태 ----------

def test_all_21_sources_have_decision():
    """수집하지 않는 출처는 반드시 사유(note)가 있어야 한다."""
    assert len(SOURCES) == 26
    for s in SOURCES.values():
        if not s.enabled:
            assert s.note, f"{s.id}: 꺼진 출처에 사유(note)가 없습니다"
        elif s.type == "generic_board":
            assert s.url and s.selectors.get("row"), f"{s.id}: 선택자가 없습니다"
    assert {s.id for s in SOURCES.values() if not s.enabled} == {
        "ntis", "iitp", "gpu", "etri", "export_capability", "sba", "klid_bid"}

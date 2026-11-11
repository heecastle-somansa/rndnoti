"""5단계 테스트: 정규화, 제외어, 분류 매칭, 중복(출처 내·출처 간), 정정, 리마인드, 구독자 (F3~F15)."""
import json
from datetime import date

import pytest

from rnd_noti.config import load_config
from rnd_noti.match import (KIND_NEW, KIND_REMINDER, KIND_REVISION, Subscriber, dedupe_cross_source,
                            dedupe_same_uid, drop_expired, match_categories, select_for_subscriber, update_seen)
from rnd_noti.models import Notice
from rnd_noti.normalize import assign_group, format_amount, norm, normalize, passes_title_filter, title_key
from rnd_noti.state import SeenEntry
from rnd_noti.subscribers import SubscriberError, load_subscribers
from tests.fakes import TEST_CONFIG

CFG = load_config(TEST_CONFIG)
KW, GROUPS = CFG.keywords, CFG.groups
SRC = {s.id: s for s in CFG.sources}
TODAY = date(2026, 11, 9)


def N(uid, title, source="g2b_bid", **kw):
    kw.setdefault("agency_group", "나라장터")
    return Notice(uid=uid, source=source, title=title, **kw)


# ---------- 정규화 ----------

def test_norm_f5():
    assert norm("망 분리") == norm("망분리")
    assert norm("VDI 구축") == "vdi구축"
    assert title_key("「2027년 망분리(고도화)」 사업") == title_key("2027년 망분리 고도화 사업")


def test_title_filter():
    assert passes_title_filter("2026 정보보호 사업 공고", ("공고", "모집"))
    assert not passes_title_filter("홈페이지 점검 안내", ("공고", "모집", "사업"))
    assert passes_title_filter("아무거나", ())


def test_portal_regroup():
    assert assign_group("정보통신기획평가원", GROUPS) == "IITP"
    assert assign_group("한국산업기술기획평가원", GROUPS) == "KEIT"
    assert assign_group("정보통신산업진흥원", GROUPS) == "NIPA"
    assert assign_group("한국연구재단", GROUPS) == "기타 기관"
    assert assign_group("", GROUPS) == "기타 기관"


def test_normalize_portal_and_board():
    iris = normalize(N("iris:1", "2027 AI보안 기술수요조사", source="iris", agency_group="통합포털",
                       org="정보통신기획평가원"), SRC["iris"], GROUPS)
    assert (iris.agency_group, iris.notice_type, iris.ministry) == ("IITP", "사전규격/수요조사", "과학기술정보통신부")
    kisa = normalize(N("kisa:1", "정보보호 지원사업 공고", source="kisa", agency_group="KISA"), SRC["kisa"], GROUPS)
    assert (kisa.agency_group, kisa.org, kisa.notice_type) == ("KISA", "KISA", "본공고")


def test_format_amount():
    assert format_amount(1_250_000_000) == "12.5억"
    assert format_amount(100_000_000) == "1.0억"
    assert format_amount(85_000_000) == "8,500만"
    assert format_amount(None) == "" and format_amount(0) == ""


# ---------- 제외어·매칭 (F6, F7) ----------

def test_match_basic_and_space_insensitive():
    m = match_categories("2027년 업무망 논리적 망 분리 고도화 및 vdi 구축", ["망분리·VDI", "클라우드·가상화"], KW)
    assert set(m) == {"망분리·VDI"}
    assert {"VDI"} <= set(m["망분리·VDI"])


def test_common_exclude_removes_from_all():
    assert match_categories("정보보호 시설경비 용역", ["보안 일반", "생체인증·출입보안"], KW) == {}


def test_category_exclude_only_that_category():
    title = "청사 출입통제 및 정보보안 시스템 구축"
    m = match_categories(title, ["보안 일반", "생체인증·출입보안"], KW)
    assert "보안 일반" not in m                    # 보안 분류에서는 출입통제 제외
    assert m["생체인증·출입보안"] == ["출입통제"]    # 생체인증 분류에서는 핵심


def test_unknown_category_ignored():
    assert match_categories("망분리", ["없는분류"], KW) == {}


# ---------- 중복·정정 (F3, F4, F14) ----------

def test_same_uid_keeps_latest_revision():
    a = N("g2b:1", "망분리 사업", revision_no=0)
    b = N("g2b:1", "[정정] 망분리 사업", revision_no=2, is_revision=True)
    c = N("g2b:1", "[정정] 망분리 사업", revision_no=1, is_revision=True)
    assert dedupe_same_uid([a, b, c]) == [b]


def test_cross_source_prefers_agency_board():
    portal = N("iris:9", "「2027 정보보호 R&D」 공고", source="iris", agency_group="IITP", deadline=date(2026, 11, 30))
    board = N("iitp:9", "2027 정보보호 R&D 공고", source="iitp", agency_group="IITP", deadline=date(2026, 11, 30))
    other = N("iris:10", "2027 정보보호 R&D 공고", source="iris", agency_group="KEIT", deadline=date(2026, 11, 30))
    kept = dedupe_cross_source([portal, board, other], {"iris", "ntis", "bizinfo"})
    assert {n.uid for n in kept} == {"iitp:9", "iris:10"}    # 기관이 다르면 별개 공고


def test_cross_source_deadline_rules():
    a = N("kisa:1", "보안 사업", source="kisa", agency_group="KISA", deadline=None)
    b = N("iris:1", "보안 사업", source="iris", agency_group="KISA", deadline=date(2026, 12, 1))
    assert len(dedupe_cross_source([a, b], {"iris"})) == 1          # 한쪽 마감일 없음 → 같은 공고
    c = N("kisa:2", "보안 사업", source="kisa", agency_group="KISA", deadline=date(2026, 11, 20))
    assert len(dedupe_cross_source([c, b], {"iris"})) == 2          # 마감일이 다름 → 다른 공고


def test_drop_expired():
    ns = [N("a", "x", deadline=date(2026, 11, 8)), N("b", "x", deadline=TODAY), N("c", "x")]
    assert [n.uid for n in drop_expired(ns, TODAY)] == ["b", "c"]


# ---------- 구독자별 선택 (F3, F4, F9, F15) ----------

ME = Subscriber("본인", "me@example.com", ["망분리·VDI", "보안 일반"])
FRIEND = Subscriber("지인", "friend@example.com", ["생체인증·출입보안"], exclude_sources=["kisia"])


def seen_of(*notices, sent=date(2026, 11, 2)):
    return {n.uid: SeenEntry(sent, n) for n in notices}


def test_new_vs_seen_vs_revision():
    old = N("g2b:1", "망분리 사업", revision_no=0, deadline=date(2026, 12, 30))
    seen = seen_of(old)
    same = N("g2b:1", "망분리 사업", revision_no=0, deadline=date(2026, 12, 30))
    assert select_for_subscriber([same], seen, ME, KW, TODAY) == []          # 두 번 실행해도 다시 안 보냄
    rev = N("g2b:1", "[정정] 망분리 사업", revision_no=1, is_revision=True, deadline=date(2026, 12, 30))
    items = select_for_subscriber([rev], seen, ME, KW, TODAY)
    assert [(i.notice.uid, i.kind) for i in items] == [("g2b:1", KIND_REVISION)]
    new = N("g2b:2", "제로트러스트 보안 고도화", deadline=date(2026, 12, 30))
    items = select_for_subscriber([new], seen, ME, KW, TODAY)
    assert items[0].kind == KIND_NEW and items[0].notice.matched == {"보안 일반": ["보안고도화", "제로트러스트"]}  # keywords.yaml 순서


def test_reminder_for_seen_deadline_within_7_days():
    soon = N("g2b:5", "VDI 구축", deadline=date(2026, 11, 16))       # D-7
    later = N("g2b:6", "VDI 구축 2", deadline=date(2026, 11, 17))    # D-8
    other = N("g2b:7", "지문인식 출입통제", deadline=date(2026, 11, 10))
    items = select_for_subscriber([], seen_of(soon, later, other), ME, KW, TODAY)
    assert [(i.notice.uid, i.kind) for i in items] == [("g2b:5", KIND_REMINDER)]
    items = select_for_subscriber([], seen_of(soon, later, other), FRIEND, KW, TODAY)
    assert [i.notice.uid for i in items] == ["g2b:7"]


def test_reminder_not_duplicated_with_revision():
    old = N("g2b:1", "망분리 사업", deadline=date(2026, 11, 12))
    rev = N("g2b:1", "[정정] 망분리 사업", revision_no=1, is_revision=True, deadline=date(2026, 11, 12))
    items = select_for_subscriber([rev], seen_of(old), ME, KW, TODAY)
    assert [i.kind for i in items] == [KIND_REVISION]


def test_subscriber_source_scope():
    kisia = N("kisia:1", "안면인식 출입보안 사업", source="kisia", agency_group="KISIA")
    assert select_for_subscriber([kisia], {}, FRIEND, KW, TODAY) == []
    only = Subscriber("x", "x@example.com", ["생체인증·출입보안"], sources=["kisia"])
    assert len(select_for_subscriber([kisia], {}, only, KW, TODAY)) == 1
    g2b = N("g2b:3", "안면인식 사업")
    assert select_for_subscriber([g2b], {}, only, KW, TODAY) == []


def test_update_seen_keeps_first_sent_and_skips_reminders():
    from rnd_noti.match import Item
    old = N("g2b:1", "망분리", deadline=date(2026, 11, 12))
    seen = seen_of(old, sent=date(2026, 10, 1))
    rev = N("g2b:1", "[정정] 망분리", revision_no=1, is_revision=True, matched={"망분리·VDI": ["망분리"]})
    new = N("g2b:2", "VDI", matched={"망분리·VDI": ["VDI"]})
    rem = N("g2b:3", "VDI 2")
    out = update_seen(seen, [Item(rev, KIND_REVISION), Item(new, KIND_NEW), Item(rem, KIND_REMINDER)], TODAY)
    assert out["g2b:1"].first_sent == date(2026, 10, 1) and out["g2b:1"].notice.revision_no == 1
    assert out["g2b:1"].notice.matched == {}                     # 구독자별 매칭 결과는 저장하지 않음
    assert out["g2b:2"].first_sent == TODAY
    assert "g2b:3" not in out


# ---------- 구독자 목록 ----------

def test_load_subscribers_active_and_warnings():
    raw = json.dumps([
        {"name": "본인", "email": "me@example.com", "active": True, "categories": ["망분리·VDI", "오타분류"]},
        {"name": "지인", "email": "f@example.com", "active": True, "categories": ["생체인증·출입보안"],
         "exclude_sources": ["kisia", "nope"]},
        {"name": "휴면", "email": "z@example.com", "active": False, "categories": ["보안 일반"]},
    ], ensure_ascii=False)
    subs, warnings = load_subscribers(raw, KW, set(SRC))
    assert [s.name for s in subs] == ["본인", "지인"]              # active: false는 발송 안 함
    assert subs[0].categories == ["망분리·VDI"]
    assert any("오타분류" in w for w in warnings) and any("nope" in w for w in warnings)
    assert not any("@" in w for w in warnings)                  # 경고에 메일 주소 없음


@pytest.mark.parametrize("raw, word", [
    ("", "비어"), ("[{", "형식 오류"), ('{"a":1}', "목록"),
    ('[{"email":"a@b.kr","categories":[]}]', "name"),
    ('[{"name":"A","email":"not-mail","categories":[]}]', "email"),
    ('[{"name":"A","email":"a@b.kr"},{"name":"A","email":"c@d.kr"}]', "중복"),
])
def test_load_subscribers_errors_hide_email(raw, word):
    with pytest.raises(SubscriberError, match=word) as exc:
        load_subscribers(raw, KW, set(SRC))
    assert "a@b.kr" not in str(exc.value) and "not-mail" not in str(exc.value)


# ---------- 공고 카테고리 (R&D / 입찰 / 바우처) ----------

def _n(uid, source, title, **kw):
    return normalize(Notice(uid=uid, source=source, title=title, **kw), SRC[source], GROUPS)


def test_track_comes_from_source_and_title_can_move_it():
    assert _n("a", "iris", "2027년 사이버보안 신규사업 공고", org="정보통신기획평가원").track == "R&D"
    assert _n("b", "bizinfo", "2027년 데이터 바우처 지원사업 수요기업 모집").track == "바우처"
    assert _n("c", "bizinfo", "중소기업 기술개발사업 시행계획 공고").track == "R&D"          # 공고명으로 이동
    assert _n("d", "nipa", "AI 바우처 지원사업 수요기업 모집").track == "바우처"            # R&D 출처 → 바우처
    assert _n("e", "kisa", "정보보호 컨설팅 용역 입찰공고").track == "입찰"                 # 공지 게시판의 입찰
    assert _n("f", "g2b_bid", "정보보호 R&D 지원사업 관리 용역").track == "입찰"            # 입찰 출처는 고정


def test_only_in_category_applies_to_its_track_only():
    chosen = ["보안 일반", "바우처·인프라 지원"]
    assert match_categories("GPU 서버 구매", chosen, KW, "입찰") == {}                       # 입찰에서는 GPU 무시
    assert match_categories("GPU 활용지원 사업 참여기업 모집", chosen, KW, "바우처") == {"바우처·인프라 지원": ["GPU"]}
    assert "보안 일반" in match_categories("정보보호 바우처 지원", chosen, KW, "바우처")     # 보안 분류는 어디서나


def test_subscriber_can_pick_source_categories():
    pool = [_n("r", "iris", "제로트러스트 기술개발 과제 공고", org="정보통신기획평가원"),
            _n("b", "g2b_bid", "제로트러스트 보안체계 구축"),
            _n("v", "bizinfo", "정보보호 해외진출 지원사업 참여기업 모집")]
    raw = json.dumps([{"name": "입찰만", "email": "a@x.example", "categories": ["보안 일반"],
                       "source_categories": ["입찰"]},
                      {"name": "전체", "email": "b@x.example", "categories": ["보안 일반"]},
                      {"name": "오타", "email": "c@x.example", "categories": ["보안 일반"],
                       "source_categories": ["입찰", "없는것"]}])
    subs, warnings = load_subscribers(raw, KW, set(SRC), GROUPS)
    picked = {s.name: [i.notice.uid for i in select_for_subscriber(pool, {}, s, KW, TODAY)] for s in subs}
    assert picked == {"입찰만": ["b"], "전체": ["r", "b", "v"], "오타": ["b"]}
    assert any("없는것" in w for w in warnings)


def test_same_bid_on_g2b_and_agency_board_is_shown_once():
    g2b = _n("g2b:1", "g2b_bid", "2027년 SW자산뱅크 통합운영 유지관리 용역", budget_krw=500_000_000,
             deadline=date(2026, 11, 30))
    board = _n("nipa_bid:1", "nipa_bid", "[조달청 입찰공고] 2027년 SW자산뱅크 통합운영 유지관리 용역")
    other = _n("kisa_bid:1", "kisa_bid", "[사전규격공개] 지능형 포렌식센터 스토리지 구매")
    kept = dedupe_cross_source([board, g2b, other], {"iris"}, {"입찰"})
    assert [n.uid for n in kept] == ["g2b:1", "kisa_bid:1"]              # 금액 있는 나라장터 쪽을 남김
    assert len(dedupe_cross_source([board, g2b], {"iris"})) == 2          # 옵션이 없으면 예전처럼 기관별 비교

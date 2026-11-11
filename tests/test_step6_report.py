"""6단계 테스트: 메일 구성·용어·Outlook 호환 규칙 (개발요청서 5장)."""
import re
from dataclasses import replace
from datetime import date

import pytest

from rnd_noti.config import load_config
from rnd_noti.match import KIND_NEW, KIND_REMINDER, KIND_REVISION, Item
from rnd_noti.models import Notice
from rnd_noti.preview import build_sample
from rnd_noti.report import GRAY, ORANGE, RED, build_report, dday, make_card, render, week_label
from tests.fakes import TEST_CONFIG

CFG = load_config(TEST_CONFIG)
TODAY = date(2026, 11, 9)


def item(uid, title, kind=KIND_NEW, group="나라장터", **kw):
    kw.setdefault("matched", {"망분리·VDI": ["망분리"]})
    return Item(Notice(uid=uid, source="g2b_bid", title=title, agency_group=group, url="https://www.g2b.go.kr/", **kw), kind)


def report_of(items, failed=()):
    return build_report(items, ["망분리·VDI", "클라우드·가상화"], CFG.groups, TODAY, date(2026, 11, 3), TODAY, list(failed))


@pytest.mark.parametrize("d, label", [
    (date(2026, 11, 9), "11월 2주차"), (date(2026, 11, 2), "11월 1주차"), (date(2026, 11, 30), "12월 1주차"),
    (date(2026, 6, 1), "6월 1주차"), (date(2026, 11, 11), "11월 2주차"),
])
def test_week_label(d, label):
    assert week_label(d) == label


def test_dday_colors():
    def color(days, pre=False):
        n = item("a", "x", deadline=date.fromordinal(TODAY.toordinal() + days),
                 notice_type="사전규격/수요조사" if pre else "본공고")
        return dday(n, TODAY)[:2]
    assert color(0) == ("D-0", RED) and color(3) == ("D-3", RED)
    assert color(4) == ("D-4", ORANGE) and color(7) == ("D-7", ORANGE)
    assert color(8) == ("D-8", GRAY)
    assert color(20, pre=True) == ("의견 D-20", ORANGE)
    assert dday(item("b", "x"), TODAY)[:2] == ("마감 미정", GRAY)


def test_card_rows_and_badges():
    c = make_card(item("a", "[정정] 망분리 사업", KIND_REVISION, org="○○공단", budget_krw=1_250_000_000,
                       contract_info="협상에 의한 계약", deadline=date(2026, 11, 12)), TODAY)
    assert c.title == "망분리 사업" and c.badges[0][0] == "정정"
    assert c.meta == "○○공단 · 12.5억 · 협상에 의한 계약"
    r = make_card(item("b", "VDI", KIND_REMINDER, deadline=date(2026, 11, 12)), TODAY)
    assert [b[0] for b in r.badges] == ["리마인드"]
    n = make_card(item("c", "VDI"), TODAY)
    assert n.meta == "본공고"                              # 기관·금액이 없으면 그 항목은 빠진다


def test_subject_counts_and_sections():
    items = [item(f"g{i}", f"망분리 {i}", deadline=date(2026, 11, 10 + i)) for i in range(7)]
    items.append(item("p1", "망연계 사전규격", notice_type="사전규격/수요조사", deadline=date(2026, 11, 13)))
    items.append(item("k1", "망분리 R&D", group="KISA"))
    items.append(item("r1", "VDI 리마인드", KIND_REMINDER, deadline=date(2026, 11, 11)))
    r = report_of(items)
    assert r.subject == "[R&D Noti] 11월 2주차 — 관련 공고 9건, 마감 임박 9건"
    assert r.heading == "이번 주 관련 공고 9건, 마감 임박 9건"
    assert (r.new_main, r.new_pre, r.urgent) == (8, 1, 9)   # D-1~7 일곱 건 + 의견 D-4 + 리마인드 D-2
    assert [s.name for s in r.sections] == ["나라장터", "KISA"]   # 3.2 순서, 빈 섹션 생략
    s = r.sections[0]
    assert s.count == 9 and len(s.cards) == 5 and len(s.rest) == 4
    days = [c.sort_key for c in s.cards + s.rest]
    assert days == sorted(days)                                # 마감 가까운 순
    assert r.categories == "망분리·VDI, 클라우드·가상화"


def test_no_deadline_goes_last():
    r = report_of([item("a", "망분리 A"), item("b", "망분리 B", deadline=date(2026, 12, 30))])
    assert [c.dday_text for c in r.sections[0].cards] == ["D-51", "마감 미정"]


def test_zero_new_week():
    r = report_of([item("r", "VDI", KIND_REMINDER, deadline=date(2026, 11, 12))])
    assert r.subject == "[R&D Noti] 11월 2주차 — 신규 공고 없음"
    assert r.heading.startswith("이번 주 신규 공고 없음")
    html, text = render(report_of([]))
    assert "이번 주 신규 공고 없음" in html and "이번 주 신규 공고 없음" in text


def test_group_by_ministry():
    groups = replace(CFG.groups, group_by="ministry")
    items = [item("a", "망분리", group="IITP", ministry="과학기술정보통신부"),
             item("b", "망분리2", group="KISA", ministry="과학기술정보통신부"),
             item("c", "망분리3", group="나라장터", ministry="나라장터")]
    r = build_report(items, [], groups, TODAY, TODAY, TODAY, [])
    assert [(s.name, s.count) for s in r.sections] == [("나라장터", 1), ("과학기술정보통신부", 2)]


def test_html_outlook_rules():
    html, text = render(report_of([item("a", "망분리", org="○○", deadline=date(2026, 11, 12))], ["ETRI 공동연구 공고"]))
    styles = " ".join(re.findall(r'style="([^"]*)"', html)).lower()
    for banned in ("display:flex", "display:grid", "position:", "float:", "@font-face", "@import"):
        assert banned not in styles and banned not in html.lower()
    assert "<style" not in html.lower()                       # 모든 스타일은 인라인
    assert "<img" not in html.lower()                         # 외부 이미지 없음
    assert "max-width:640px" in html
    assert "'맑은 고딕','Malgun Gothic',Arial,sans-serif" in html
    assert 'bgcolor="#f9fafb"' in html                        # 배경색은 bgcolor도 함께
    for href in re.findall(r'href="([^"]+)"', html):
        assert href.startswith("https://")                    # 링크는 절대 URL
    assert "합계" not in html and "예산" not in html          # 합계 예산 카드 없음
    for term in ("신규 공고", "사전규격/수요조사", "마감 임박", "분류/키워드", "11월 2주차"):
        assert term in html
    assert "수집 실패 출처: ETRI 공동연구 공고" in html and "수집 실패 출처: ETRI 공동연구 공고" in text
    assert "분류/키워드 변경이나 수신 중단은 회신으로 알려주세요." in text


def test_summary_line_hidden_in_phase1():
    html, _ = render(report_of([item("a", "망분리")]))
    assert html.count("color:#344054;\">") == 0               # 3행(AI 요약) 줄 없음
    with_summary = item("b", "망분리", summary="요약 문장")
    html2, _ = render(report_of([with_summary]))
    assert "요약 문장" in html2                                # 2단계용 자리는 준비됨


def test_sample_preview_files(tmp_path):
    paths = build_sample(tmp_path)
    names = sorted(p.name for p in paths)
    assert names == ["preview_본인.html", "preview_본인.txt", "preview_지인.html", "preview_지인.txt"]
    me = (tmp_path / "preview_본인.txt").read_text(encoding="utf-8")
    friend = (tmp_path / "preview_지인.txt").read_text(encoding="utf-8")
    assert "시설경비" not in me and "CCTV" not in me           # 공통 제외 / 보안 분류별 제외
    assert "CCTV 및 출입통제" in friend                         # 생체인증 분류에서는 포함
    assert "KISIA" not in friend                               # exclude_sources
    assert "[리마인드]" in me and "[리마인드]" in friend
    assert "@" not in me.split("\n", 1)[1]                     # 본문에 메일 주소 없음


def test_mail_is_split_by_category_bands_then_agency():
    items = [item("b1", "망분리 구축", track="입찰"),
             item("r1", "망분리 기술개발 과제", group="IITP", track="R&D"),
             item("r2", "망분리 실증 과제", group="KISA", track="R&D"),
             item("v1", "망분리 바우처 지원사업", group="KISA", track="바우처")]
    r = report_of(items)
    assert [(s.track_label, s.name, s.first_in_track) for s in r.sections] == [
        ("부처별 R&D 공고", "IITP", True), ("부처별 R&D 공고", "KISA", False),
        ("나라장터·기관 입찰 공고", "나라장터", True), ("바우처·지원사업 공고", "KISA", True)]
    html, text = render(r)
    assert html.count("부처별 R&amp;D 공고 · 2건") == 1 and "바우처·지원사업 공고 · 1건" in html
    assert text.index("부처별 R&D 공고") < text.index("나라장터·기관 입찰 공고") < text.index("바우처·지원사업 공고")

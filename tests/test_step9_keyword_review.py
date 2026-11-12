"""2026-11-12 키워드 검토: 실제 오탐 공고명으로 확인."""
import pytest

from rnd_noti.config import Category, KeywordConfig
from rnd_noti.match import match_categories, region_ok

KW = KeywordConfig(exclude=("경비",), categories={
    "EP": Category("EP", ("EDR", "APT", "백신SW", "백신프로그램", "단말보안")),
    "VDI": Category("VDI", ("VDI", "가상PC", "DaaS")),
    "판로": Category("판로", ("수출", "전시회"), require=("ICT", "SW", "보안", "AI")),
})


@pytest.mark.parametrize("title", [
    "***의료기기*** 자동회전조직절편기(Automated Rotary Microtome) 구매",
    "AWS Bedrock 및 AI통합 포털 구축",
    "2026년 대상포진 예방접종 백신 구입(단가계약)",
])
def test_false_positives_removed(title):
    assert match_categories(title, ["EP"], KW) == {}


@pytest.mark.parametrize("title, cat", [
    ("***경쟁입찰***이메일 APT 보안 라이선스 외 3종 구매", "EP"),
    ("메일분석용APT시스템 외 2종 유지보수", "EP"),
    ("국립한국교통대학교 백신프로그램(V3 Campus Pack) 연간 사용권 구입", "EP"),
    ("단말 보안 솔루션(백신) 구매", "EP"),
    ("AWS EDR 도입", "EP"),
    ("신청사 가상PC(VDI) 라이선스 임차 계약", "VDI"),
    ("NHN Cloud Virtual Desktop 서비스(DaaS)", "VDI"),
])
def test_true_positives_kept(title, cat):
    assert cat in match_categories(title, [cat], KW)


def test_require_condition():
    assert match_categories("[전남] 농수산물 수출 경쟁력 강화 지원사업", ["판로"], KW) == {}
    assert "판로" in match_categories("정보보안 기업 해외 전시회 참가 지원", ["판로"], KW)
    assert "판로" in match_categories("ICT 중소기업 수출 지원", ["판로"], KW)


def test_region_filter():
    assert region_ok("[경기] 성남시 판로지원 사업", ["서울", "경기"])
    assert not region_ok("[강원] 횡성군 지식재산 지원", ["서울", "경기"])
    assert region_ok("[전남광주] 구례군 공고", ["전남"])
    assert region_ok("2026년 K-BUSINESS DAY 참여기업 모집", ["서울"])   # 전국 공고는 항상 통과
    assert region_ok("[긴급] 공고", ["서울"])                          # 지역이 아닌 태그
    assert region_ok("[강원] 공고", [])                                 # 설정 없으면 전체

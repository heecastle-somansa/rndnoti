"""1단계(기본 골격) 테스트: 공고 스키마, 설정 읽기, 상태 저장, 어댑터 레지스트리."""
from datetime import date

import pytest

from rnd_noti import state
from rnd_noti.config import ConfigError, SourceConfig, load_config, load_groups, load_keywords, load_sources
from rnd_noti.models import NOTICE_TYPE_PRE, Notice
from rnd_noti.sources import registry
from rnd_noti.sources.base import SourceAdapter, SourceError


# ---------- models ----------

def test_notice_roundtrip_keeps_all_fields():
    n = Notice(uid="g2b:20261100001", source="g2b_bid", title="망분리 고도화", org="○○공단",
               budget_krw=1_250_000_000, posted_at=date(2026, 11, 9), deadline=date(2026, 11, 20),
               notice_type=NOTICE_TYPE_PRE, is_revision=True, revision_no=2,
               matched={"망분리·VDI": ["망분리"]})
    assert Notice.from_dict(n.to_dict()) == n


def test_notice_empty_values_and_reserved_summary():
    n = Notice(uid="iitp:1", source="iitp", title="공고")
    d = n.to_dict()
    assert d["deadline"] is None and d["budget_krw"] is None
    assert d["summary"] == ""          # 2단계용 예약 필드는 빈 값
    assert Notice.from_dict(d) == n


def test_notice_rejects_bad_values():
    with pytest.raises(ValueError):
        Notice(uid="", source="x", title="t")
    with pytest.raises(ValueError):
        Notice(uid="a", source="x", title="t", notice_type="입찰")


# ---------- config (실제 config/ 폴더) ----------

def test_real_config_loads():
    cfg = load_config()
    assert len(cfg.sources) == 21
    assert len({s.id for s in cfg.sources}) == 21
    assert cfg.groups.names == [
        "나라장터", "과학기술정보통신부", "IITP", "NIPA", "GPU추진단", "NIA", "KISA", "KISIA",
        "ETRI", "KEIT", "중소벤처기업부", "수출지원", "SBA", "기타 기관"]
    assert cfg.groups.group_by == "agency"
    assert set(cfg.keywords.categories) == {
        "망분리·VDI", "클라우드·가상화", "DLP·정보유출", "엔드포인트", "보안 일반", "생체인증·출입보안"}
    assert "소방" in cfg.keywords.exclude
    # CCTV·출입통제는 공통 제외가 아니라 보안 분류별 제외
    assert "출입통제" not in cfg.keywords.exclude
    assert "출입통제" in cfg.keywords.categories["보안 일반"].exclude
    assert "출입통제" in cfg.keywords.categories["생체인증·출입보안"].keywords
    portal = [s.id for s in cfg.sources if s.group == cfg.groups.portal_group]
    assert portal == ["iris", "ntis", "bizinfo"]


def test_real_config_has_no_secrets():
    from rnd_noti.config import CONFIG_DIR
    for path in CONFIG_DIR.glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        assert "@" not in text.replace("a@href", ""), f"{path.name}에 메일 주소로 보이는 값이 있습니다"
        assert "serviceKey" not in text


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_disabling_source_needs_no_code_change(tmp_path):
    p = _write(tmp_path, "sources.yaml", """
- {id: a, name: A, group: IITP, type: generic_board, enabled: false}
- {id: b, name: B, group: 통합포털, type: "custom:iris"}
""")
    sources = load_sources(p, load_groups())
    assert [s.id for s in sources if s.enabled] == ["b"]


@pytest.mark.parametrize("body, word", [
    ("- {id: a, name: A, group: 없는그룹, type: generic_board}", "group"),
    ("- {id: a, name: A, group: IITP, type: rss}", "type"),
    ("- {id: a, name: A, group: IITP, type: generic_board, enabld: true}", "오타"),
    ("- {id: a, name: A, group: IITP, type: generic_board}\n"
     "- {id: a, name: B, group: IITP, type: generic_board}", "중복"),
    ("- {id: a, name: A, group: IITP, type: generic_board, notice_type: 입찰}", "notice_type"),
    ("- {name: A, group: IITP, type: generic_board}", "id"),
])
def test_bad_sources_give_clear_error(tmp_path, body, word):
    p = _write(tmp_path, "sources.yaml", body)
    with pytest.raises(ConfigError, match=word):
        load_sources(p, load_groups())


def test_bad_keywords_and_groups(tmp_path):
    with pytest.raises(ConfigError):
        load_keywords(_write(tmp_path, "k.yaml", "categories:\n  빈분류: {}\n"))
    with pytest.raises(ConfigError, match="group_by"):
        load_groups(_write(tmp_path, "g.yaml", "group_by: team\ngroups: [{name: 기타 기관}]\n"))
    with pytest.raises(ConfigError, match="형식 오류"):
        load_keywords(_write(tmp_path, "bad.yaml", "categories: [a, b"))


# ---------- state ----------

def test_collection_window():
    today = date(2026, 11, 9)
    assert state.collection_window(None, today) == (date(2026, 11, 2), today)               # 최초 7일
    assert state.collection_window(date(2026, 11, 2), today) == (date(2026, 11, 3), today)  # 평소
    assert state.collection_window(date(2026, 10, 19), today) == (date(2026, 10, 20), today)  # 건너뛴 주
    assert state.collection_window(today, today) == (today, today)                          # 같은 날 재실행


def test_last_run_roundtrip(tmp_path):
    p = tmp_path / "last_run.json"
    assert state.load_last_run(p) is None
    state.save_last_run(date(2026, 11, 9), p)
    assert state.load_last_run(p) == date(2026, 11, 9)


def test_seen_roundtrip_and_prune(tmp_path):
    p = tmp_path / "seen.json"
    assert state.load_seen(p) == {}
    today = date(2026, 11, 9)
    mk = lambda uid: Notice(uid=uid, source="iitp", title="한글 공고", deadline=date(2026, 12, 1))
    seen = {
        "old": state.SeenEntry(date(2026, 8, 10), mk("old")),      # 91일 전 → 삭제
        "edge": state.SeenEntry(date(2026, 8, 11), mk("edge")),    # 정확히 90일 → 유지
        "new": state.SeenEntry(today, mk("new")),
    }
    state.save_seen(seen, p)
    assert "한글 공고" in p.read_text(encoding="utf-8")            # 한글이 깨지지 않고 저장됨
    loaded = state.load_seen(p)
    assert loaded == seen
    assert set(state.prune_seen(loaded, today)) == {"edge", "new"}


# ---------- registry ----------

def test_registry_register_and_create():
    @registry.register("custom:dummy_for_test")
    class Dummy(SourceAdapter):
        def fetch(self, since, until):
            return [Notice(uid="d:1", source=self.cfg.id, title="t")]

    cfg = SourceConfig(id="d", name="D", group="IITP", type="custom:dummy_for_test")
    adapter = registry.create_adapter(cfg, {"DATA_GO_KR_KEY": "x"})
    assert adapter.fetch(date(2026, 11, 1), date(2026, 11, 9))[0].source == "d"


def test_registry_unknown_type_is_source_error():
    with pytest.raises(SourceError, match="어댑터가 없습니다"):
        registry.get_adapter_class("custom:does_not_exist")

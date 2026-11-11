"""7단계 테스트: 전체 실행(수집→매칭→리포트→발송→상태) — 가짜 출처와 가짜 메일 서버 사용."""
import json
from dataclasses import replace
from datetime import date

import pytest

from rnd_noti import state as st
from rnd_noti.config import load_config
from rnd_noti.mailer import MailError, Mailer, MailSettings, build_message
from rnd_noti.main import run_check_sources, run_weekly
from rnd_noti.models import Notice
from rnd_noti.sources.base import SourceError
from tests.fakes import TEST_CONFIG

CFG = load_config(TEST_CONFIG)
TODAY = date(2026, 11, 9)
SUBS = [
    {"name": "본인", "email": "me@corp.example", "active": True, "categories": ["망분리·VDI", "보안 일반"]},
    {"name": "지인", "email": "friend@suprema.example", "active": True, "categories": ["생체인증·출입보안"],
     "exclude_sources": ["kisia"]},
]
ENV = {"SMTP_USER": "sender@gmail.example", "SMTP_PASSWORD": "app-password-123", "ADMIN_EMAIL": "admin@corp.example",
       "SUBSCRIBERS_JSON": json.dumps(SUBS, ensure_ascii=False), "DATA_GO_KR_KEY": "KEY123"}
ALL_SECRETS = ["me@corp.example", "friend@suprema.example", "sender@gmail.example", "app-password-123",
               "admin@corp.example", "KEY123"]


class FakeSMTP:
    instances = []

    def __init__(self, host, port):
        self.host, self.port, self.sent, self.fail_for = host, port, [], set()
        FakeSMTP.instances.append(self)

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        if msg["To"] in self.fail_for:
            import smtplib
            raise smtplib.SMTPRecipientsRefused({msg["To"]: (550, b"no")})
        self.sent.append(msg)
        return {}

    def quit(self):
        pass


@pytest.fixture(autouse=True)
def reset_smtp():
    FakeSMTP.instances = []


def mailer(fail_for=()):
    def factory(settings):
        def smtp(host, port):
            s = FakeSMTP(host, port)
            s.fail_for = set(fail_for)
            return s
        return Mailer(settings, smtp_factory=smtp)
    return factory


def sent_messages():
    return [m for s in FakeSMTP.instances for m in s.sent]


DATA = {
    "g2b_bid": [Notice(uid="g2b:1", source="g2b_bid", title="2027 논리적 망분리 고도화", org="○○공단",
                       budget_krw=1_250_000_000, deadline=date(2026, 11, 20), posted_at=date(2026, 11, 5)),
                Notice(uid="g2b:2", source="g2b_bid", title="통합 출입관리 안면인식 시스템", deadline=date(2026, 11, 25),
                       posted_at=date(2026, 11, 5)),
                Notice(uid="g2b:3", source="g2b_bid", title="청사 시설경비 용역", posted_at=date(2026, 11, 5))],
    "iris": [Notice(uid="iris:1", source="iris", title="「제로트러스트 실증 지원사업」 공고", org="한국인터넷진흥원",
                    agency_group="통합포털", deadline=date(2026, 11, 30), posted_at=date(2026, 11, 5))],
    "kisa": [Notice(uid="kisa:1", source="kisa", title="홈페이지 점검 안내", posted_at=date(2026, 11, 5)),
             Notice(uid="kisa:2", source="kisa", title="제로트러스트 실증 지원사업 공고", posted_at=date(2026, 11, 5))],
    "kisia": [Notice(uid="kisia:1", source="kisia", title="안면인식 보안 해외진출 모집", posted_at=date(2026, 11, 5))],
}


def adapters(fail=(), data=DATA):
    class A:
        def __init__(self, src):
            self.src = src

        def fetch(self, since, until):
            if self.src.id in fail:
                raise SourceError(f"접속 실패 serviceKey={ENV['DATA_GO_KR_KEY']}")
            return [replace(n, source_name=self.src.name, agency_group=n.agency_group or self.src.group)
                    for n in data.get(self.src.id, [])]
    return lambda src, env: A(src)


def run(tmp_path, env=ENV, cfg=CFG, dry=False, fail=(), fail_mail=(), today=TODAY):
    return run_weekly(cfg, env, today, tmp_path / "out", tmp_path / "state", dry_run=dry,
                      adapter_factory=adapters(fail), mailer_factory=mailer(fail_mail))


def body(msg):
    return msg.get_body(("plain",)).get_content()


# ---------- 정상 발송 ----------

def test_normal_run_sends_individual_mails_and_updates_state(tmp_path, capsys):
    assert run(tmp_path) == 0
    msgs = sent_messages()
    assert [m["To"] for m in msgs] == ["me@corp.example", "friend@suprema.example"]   # 한 통에 한 사람
    for m in msgs:
        assert m.get_content_type() == "multipart/alternative"
        assert [p.get_content_type() for p in m.iter_parts()] == ["text/plain", "text/html"]
        assert "Cc" not in m and "Bcc" not in m
        assert m["From"] == "R&D Noti <sender@gmail.example>"
    me, friend = body(msgs[0]), body(msgs[1])
    assert "논리적 망분리" in me and "제로트러스트" in me and "시설경비" not in me
    assert me.count("제로트러스트 실증") == 1                 # IRIS·KISA 게시판 중복은 하나만(F14)
    assert "「제로트러스트" not in me                          # 기관 게시판(KISA) 쪽이 남음
    assert "안면인식" in friend and "해외진출" not in friend   # 지인은 kisia 제외
    assert "홈페이지 점검" not in me                           # KISA 제목 필터
    assert st.load_last_run(tmp_path / "state" / "last_run.json") == TODAY
    seen = st.load_seen(tmp_path / "state" / "seen.json")
    assert set(seen) == {"g2b:1", "g2b:2", "kisa:2"}           # IRIS 중복분은 보내지 않았으므로 없음
    log = capsys.readouterr().out
    for secret in ALL_SECRETS:
        assert secret not in log                               # 로그에 주소·키 없음
    assert "본인" in log and "| 나라장터 입찰공고 | g2b_bid | 성공 |" in log


def test_second_run_same_week_does_not_resend(tmp_path):
    run(tmp_path)
    FakeSMTP.instances = []
    assert run(tmp_path) == 0
    me = body(sent_messages()[0])
    assert "신규 공고 없음" in sent_messages()[0]["Subject"]
    assert "논리적 망분리" not in me                           # 마감 D-11 → 리마인드 대상 아님


def test_reminder_next_week(tmp_path):
    run(tmp_path)
    FakeSMTP.instances = []
    run(tmp_path, today=date(2026, 11, 16))                    # g2b:1 마감 11-20 → D-4
    me = body(sent_messages()[0])
    assert "[리마인드] 2027 논리적 망분리 고도화" in me


def test_zero_week_still_sends(tmp_path):
    empty = lambda src, env: type("A", (), {"fetch": lambda self, a, b: []})()
    assert run_weekly(CFG, ENV, TODAY, tmp_path / "o", tmp_path / "s", dry_run=False,
                      adapter_factory=empty, mailer_factory=mailer()) == 0
    assert all("신규 공고 없음" in m["Subject"] for m in sent_messages()) and len(sent_messages()) == 2


# ---------- 실패 처리 ----------

def test_partial_source_failure_is_listed_in_footer(tmp_path, capsys):
    assert run(tmp_path, fail={"iris"}) == 0
    me = body(sent_messages()[0])
    assert "수집 실패 출처: IRIS 통합 사업공고" in me
    log = capsys.readouterr().out
    assert "serviceKey=***" in log and "KEY123" not in log


def test_all_sources_fail_sends_admin_only(tmp_path):
    every = {s.id for s in CFG.sources}
    assert run(tmp_path, fail=every) == 1
    msgs = sent_messages()
    assert [m["To"] for m in msgs] == ["admin@corp.example"]
    assert "모든 출처 수집 실패" in msgs[0]["Subject"]
    assert "KEY123" not in body(msgs[0])
    assert not (tmp_path / "state" / "last_run.json").exists()


def test_partial_send_failure_keeps_state(tmp_path):
    assert run(tmp_path, fail_mail={"friend@suprema.example"}) == 1
    assert not (tmp_path / "state" / "last_run.json").exists()   # 발송이 모두 성공해야 갱신
    admin = [m for m in sent_messages() if m["To"] == "admin@corp.example"][0]
    assert "지인" in body(admin) and "friend@" not in body(admin)


def test_bad_subscribers_json(tmp_path):
    env = dict(ENV, SUBSCRIBERS_JSON="[{oops")
    assert run(tmp_path, env=env) == 1
    assert [m["To"] for m in sent_messages()] == ["admin@corp.example"]


# ---------- 설정만으로 반영 (완료 기준) ----------

def test_disable_source_and_pause_subscriber_without_code(tmp_path):
    cfg = replace(CFG, sources=tuple(replace(s, enabled=False) if s.id == "kisa" else s for s in CFG.sources))
    subs = [dict(SUBS[0]), dict(SUBS[1], active=False)]
    env = dict(ENV, SUBSCRIBERS_JSON=json.dumps(subs, ensure_ascii=False))
    assert run(tmp_path, env=env, cfg=cfg) == 0
    msgs = sent_messages()
    assert [m["To"] for m in msgs] == ["me@corp.example"]
    assert "「제로트러스트 실증 지원사업」 공고" in body(msgs[0])   # KISA 게시판을 끄면 IRIS 쪽이 대신 남음


def test_other_sender_account_by_secrets_only(tmp_path):
    env = dict(ENV, SMTP_HOST="smtp.company.example", SMTP_PORT="2525", SMTP_USER="noti@company.example",
               MAIL_FROM_NAME="사업 공고 알림", MAIL_REPLY_TO="planner@company.example")
    run(tmp_path, env=env)
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.user) == ("smtp.company.example", 2525, "noti@company.example")
    m = sent_messages()[0]
    assert "noti@company.example" in m["From"] and m["Reply-To"] == "planner@company.example"


# ---------- 미리보기·출처 점검 ----------

def test_dry_run_saves_files_without_sending_or_state(tmp_path):
    assert run(tmp_path, dry=True) == 0
    assert sent_messages() == []
    files = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert files == ["01_본인.html", "01_본인.txt", "02_지인.html", "02_지인.txt"]
    assert not (tmp_path / "state" / "last_run.json").exists()
    for p in (tmp_path / "out").iterdir():
        text = p.read_text(encoding="utf-8")
        assert "me@corp.example" not in text and "friend@" not in text


def test_dry_run_without_subscribers_uses_examples(tmp_path):
    env = {k: v for k, v in ENV.items() if k != "SUBSCRIBERS_JSON"}
    assert run(tmp_path, env=env, dry=True) == 0
    assert [p.name for p in (tmp_path / "out").glob("*.html")] == ["01_미리보기_전체분류.html"]


def test_check_sources_reports_all_21(tmp_path, capsys):
    rc = run_check_sources(CFG, ENV, TODAY, tmp_path, adapter_factory=adapters(fail={"msit"}),
                           mailer_factory=mailer())
    assert rc == 0
    log = capsys.readouterr().out
    assert log.count("\n| ") >= 21
    assert "| IITP (현)사업공고 | iitp | 꺼짐 |" in log and "robots.txt" in log
    assert "| 과학기술정보통신부 사업공고 | msit | 실패 |" in log
    html = (tmp_path / "check_sources.html").read_text(encoding="utf-8")
    assert "출처 점검 결과" in html and "제로트러스트" in html      # 최근 공고 표시
    msgs = sent_messages()
    assert [m["To"] for m in msgs] == ["admin@corp.example"]
    assert not (tmp_path / "state").exists()


# ---------- 메일 형식 ----------

def test_build_message_and_settings():
    s = MailSettings.from_env({"SMTP_USER": "a@b.example", "SMTP_PASSWORD": "x"})
    assert (s.host, s.port, s.from_name) == ("smtp.gmail.com", 587, "R&D Noti")
    m = build_message(s, "to@c.example", "제목", "<b>본문</b>", "본문")
    assert m["Reply-To"] == "a@b.example" and m.is_multipart()
    with pytest.raises(MailError) as exc:
        MailSettings.from_env({"SMTP_USER": "a@b.example"})
    assert "SMTP_PASSWORD" in str(exc.value)


def test_broken_config_reports_clear_error_instead_of_crash(monkeypatch, capsys):
    """설정 파일을 잘못 고치면 원인을 한글로 알리고 종료한다(메일·상태 변화 없음)."""
    from rnd_noti import main as m
    from rnd_noti.config import ConfigError

    def broken():
        raise ConfigError("keywords.yaml: 분류 '빈분류'에 keywords가 없습니다")
    monkeypatch.setattr(m, "load_config", broken)
    assert m.main(["--mode", "dry-run"]) == 1
    assert "설정 파일 오류: keywords.yaml" in capsys.readouterr().out


@pytest.mark.liveconfig
def test_validate_mode_checks_real_config_only(capsys):
    from rnd_noti import main as m
    assert m.main(["--mode", "validate"]) == 0
    assert "설정 파일 정상" in capsys.readouterr().out


# ---------- 출처 추적: 실패 알림·연속 실패·복구 후 보충 수집·주소 이동 ----------

def admin_mails():
    return [m for m in sent_messages() if m["To"] == "admin@corp.example"]


def test_source_failure_alerts_admin_and_tracks_streak(tmp_path):
    assert run(tmp_path, fail={"kisa"}) == 0
    assert len(admin_mails()) == 1 and "출처 확인 필요 1건" in admin_mails()[0]["Subject"]
    text = body(admin_mails()[0])
    assert "[실패 1회 연속] KISA 공지사항 (kisa)" in text and "2026-11-02부터" in text
    assert "KEY123" not in text
    health = st.load_health(tmp_path / "state" / "source_health.json")
    assert health["kisa"].streak == 1 and health["kisa"].since == date(2026, 11, 2)

    # 다음 주에도 실패 → 2회 연속, 못 모은 시작일은 그대로
    assert run(tmp_path, fail={"kisa"}, today=date(2026, 11, 16)) == 0
    health = st.load_health(tmp_path / "state" / "source_health.json")
    assert health["kisa"].streak == 2 and health["kisa"].since == date(2026, 11, 2)
    assert "[실패 2회 연속]" in body(admin_mails()[-1])


def test_recovered_source_catches_up_missed_period(tmp_path):
    assert run(tmp_path, fail={"kisa"}) == 0                      # 11/9: kisa 실패
    FakeSMTP.instances = []
    asked = {}

    class Spy:
        def __init__(self, src):
            self.src = src

        def fetch(self, since, until):
            asked[self.src.id] = since
            return [replace(n, source_name=self.src.name, agency_group=n.agency_group or self.src.group)
                    for n in DATA.get(self.src.id, [])]
    rc = run_weekly(CFG, ENV, date(2026, 11, 16), tmp_path / "out", tmp_path / "state", dry_run=False,
                    adapter_factory=lambda src, env: Spy(src), mailer_factory=mailer())
    assert rc == 0
    assert asked["kisa"] == date(2026, 11, 2)                     # 실패했던 주부터 다시
    assert asked["nipa"] == date(2026, 11, 10)                    # 다른 출처는 평소대로
    assert st.load_health(tmp_path / "state" / "source_health.json") == {}   # 복구되면 기록에서 빠짐
    assert not admin_mails()


def test_no_alert_mail_when_everything_is_fine(tmp_path):
    assert run(tmp_path) == 0
    assert not admin_mails()


def test_moved_url_is_detected_and_reported(tmp_path):
    from rnd_noti.sources.http import moved_to

    class R:
        def __init__(self, url, codes):
            self.url, self.history = url, [type("H", (), {"status_code": c})() for c in codes]
    old = "https://cont.kisa.or.kr/advertise/list?page=1"
    assert moved_to(old, R("https://www.kisa.or.kr/401", [302])) == "https://www.kisa.or.kr/401"      # 도메인 변경
    assert moved_to("https://a.kr/old/list", R("https://a.kr/new/list", [301])) == "https://a.kr/new/list"  # 영구 이동
    assert moved_to("http://a.kr/list", R("https://www.a.kr/list", [301])) is None                    # https·www만 다름
    assert moved_to("https://a.kr/list", R("https://a.kr/login", [302])) is None                      # 임시 이동은 무시
    assert moved_to("https://a.kr/list", R("https://a.kr/list", [])) is None

    class Moved:
        def __init__(self, src):
            self.http = type("H", (), {"moved": {"https://old.example/list": "https://new.example/list"}
                                       if src.id == "nipa" else {}})()

        def fetch(self, since, until):
            return []
    rc = run_weekly(CFG, ENV, TODAY, tmp_path / "out", tmp_path / "state", dry_run=False,
                    adapter_factory=lambda src, env: Moved(src), mailer_factory=mailer())
    assert rc == 0
    text = body(admin_mails()[0])
    assert "[주소 변경] NIPA 사업공고 목록 (nipa)" in text and "https://new.example/list" in text

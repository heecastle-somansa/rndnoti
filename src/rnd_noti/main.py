"""실행 진입점: 수집 → 정규화 → 매칭 → 리포트 → 발송 (개발요청서 2장).

    python -m rnd_noti.main --mode normal          정상: 메일 발송 + 상태 갱신
    python -m rnd_noti.main --mode dry-run         미리보기: HTML 파일만 저장, 발송·상태 갱신 없음
    python -m rnd_noti.main --mode check_sources   출처 점검: 출처별 수집 결과만 운영자에게
    python -m rnd_noti.main --mode validate        설정 검사: config/*.yaml 형식만 확인 (접속·발송 없음)

비밀값은 환경변수(GitHub Secrets)에서 읽는다. 로그에는 구독자 name만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Mapping

from . import state as st
from .config import AppConfig, ConfigError, SourceConfig, load_config
from .mailer import MailError, Mailer, MailSettings, build_message, describe_bad_value, find_addresses
from .match import (Item, Subscriber, dedupe_cross_source, dedupe_same_uid, drop_expired,
                    select_for_subscriber, update_seen)
from .models import Notice
from .normalize import normalize, passes_title_filter
from .report import build_report, render, render_named
from .sources.base import SourceError
from .sources.http import mask
from .sources.registry import create_adapter
from .subscribers import SubscriberError, load_subscribers

ROOT = Path(__file__).resolve().parents[2]
CHECK_DAYS = 30            # 출처 점검 모드의 조회 기간 (게시판)
API_CHECK_DAYS = 7         # 출처 점검 모드의 조회 기간 (나라장터 API: 건수가 많아 하루 호출 한도를 아끼려고 짧게)
SECRET_KEYS = ("DATA_GO_KR_KEY", "SMTP_PASSWORD", "SMTP_USER", "ADMIN_EMAIL", "MAIL_REPLY_TO")


@dataclass
class SourceResult:
    source: SourceConfig
    status: str                      # 성공 | 실패 | 꺼짐
    notices: list[Notice] = field(default_factory=list)
    error: str = ""
    warnings: list[str] = field(default_factory=list)   # 성공했지만 확인이 필요한 점 (예: 주소 이동)
    since: date | None = None                            # 이 출처에 실제로 적용한 수집 시작일
    calls: int = 0                                       # 이 출처에 보낸 요청 수


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _secrets(env: Mapping[str, str]) -> list[str]:
    values = [env.get(k, "") for k in SECRET_KEYS]
    values += find_addresses(env.get("ADMIN_EMAIL", "")) + find_addresses(env.get("SMTP_USER", ""))
    try:
        values += [s.get("email", "") for s in json.loads(env.get("SUBSCRIBERS_JSON") or "[]")]
    except (ValueError, AttributeError):
        pass
    return [v for v in values if v]


def mask_in_actions(env: Mapping[str, str]) -> None:
    """GitHub Actions 로그에서 구독자 주소 등이 혹시라도 보이지 않게 가린다(이중 안전장치)."""
    if env.get("GITHUB_ACTIONS") == "true":
        for value in _secrets(env):
            print(f"::add-mask::{value}", flush=True)


# ---------- 수집 ----------

def collect(cfg: AppConfig, env: Mapping[str, str], since: date, until: date,
            include_disabled: bool = False, adapter_factory: Callable = create_adapter,
            health: Mapping[str, st.SourceHealth] | None = None,
            since_for: Callable[[SourceConfig], date] | None = None) -> list[SourceResult]:
    results = []
    health = health or {}
    secrets = _secrets(env)
    for src in cfg.sources:
        if not src.enabled:
            if include_disabled:
                results.append(SourceResult(src, "꺼짐", error=src.note))
            continue
        # 지난번에 실패했던 출처는 그때 못 모은 기간부터 다시 모은다(이미 보낸 공고는 seen이 걸러 줌)
        src_since = since_for(src) if since_for else st.catch_up_since(since, until, health.get(src.id))
        adapter = None
        try:
            adapter = adapter_factory(src, env)
            raw = adapter.fetch(src_since, until)
            notices = [normalize(n, src, cfg.groups) for n in raw if passes_title_filter(n.title, src.title_filter)]
            result = SourceResult(src, "성공", notices)
        except SourceError as exc:
            result = SourceResult(src, "실패", error=mask(str(exc), secrets))
        except Exception as exc:                              # 예상 못 한 오류도 그 출처만 실패 처리
            result = SourceResult(src, "실패", error=mask(f"{type(exc).__name__}: {exc}", secrets)[:300])
        result.since = src_since
        result.calls = int(getattr(getattr(adapter, "http", None), "calls", 0) or 0)
        moved = getattr(getattr(adapter, "http", None), "moved", None) or {}
        result.warnings = [mask(f"주소가 옮겨졌습니다: {old} → {new} (sources.yaml의 url을 새 주소로 바꿔 주세요)", secrets)
                           for old, new in moved.items()]
        results.append(result)
    return results


def source_table(results: list[SourceResult]) -> str:
    lines = ["| 출처 | id | 카테고리 | 결과 | 건수 | 호출 | 비고 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in results:
        note = " / ".join([r.error or r.source.note, *r.warnings]).strip(" /").replace("|", "/")
        lines.append(f"| {r.source.name} | {r.source.id} | {r.source.track} | {r.status} | {len(r.notices)} | {r.calls} | {note} |")
    return "\n".join(lines)


def write_summary(env: Mapping[str, str], text: str) -> None:
    path = env.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n\n")


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name)


# ---------- 운영자 알림 ----------

def send_admin(env: Mapping[str, str], subject: str, html: str, text: str, mailer_factory=Mailer) -> bool:
    raw = (env.get("ADMIN_EMAIL") or "").strip()
    if not raw:
        log("ADMIN_EMAIL이 없어 운영자 알림 메일을 보내지 않았습니다")
        return False
    admins = find_addresses(raw)          # 따옴표·이름이 섞였거나 여러 주소를 적어도 주소만 골라 쓴다
    if not admins:
        log(f"운영자 알림 메일 발송 실패: ADMIN_EMAIL에서 메일 주소를 찾지 못했습니다 ({describe_bad_value(raw)}). "
            "Secrets의 ADMIN_EMAIL에 주소만(예: name@company.com) 다시 넣어 주세요")
        return False
    try:
        settings = MailSettings.from_env(env)
        with mailer_factory(settings) as m:
            for admin in admins:           # 운영자가 여럿이어도 한 통에 한 사람씩
                m.send(build_message(settings, admin, subject, html, text))
        log(f"운영자 알림 메일 발송 완료 ({len(admins)}명)")
        return True
    except MailError as exc:
        log(f"운영자 알림 메일 발송 실패: {exc}")
        return False


def admin_error(env, title: str, lines: list[str], mailer_factory=Mailer, *,
                subject: str = "", footer: str = "") -> None:
    subject = subject or f"[R&D Noti] 오류: {title}"
    html = render_named("admin_error.html", subject=subject, title=title, lines=lines, footer=footer)
    send_admin(env, subject, html, "\n".join([title, *lines]), mailer_factory)


# ---------- 출처 추적 (실패·주소 이동) ----------

def next_health(results: list[SourceResult], prev: Mapping[str, st.SourceHealth]) -> dict[str, st.SourceHealth]:
    """이번 실행 결과로 '실패 중인 출처' 기록을 새로 만든다. 성공한 출처는 기록에서 빠진다."""
    out = {}
    for r in results:
        if r.status != "실패":
            continue
        old = prev.get(r.source.id)
        out[r.source.id] = st.SourceHealth(streak=(old.streak if old else 0) + 1,
                                           since=old.since if old else r.since, error=r.error)
    return out


def source_alert_lines(results: list[SourceResult], health: Mapping[str, st.SourceHealth]) -> list[str]:
    """운영자에게 알릴 출처 문제(실패·주소 이동). 없으면 빈 목록."""
    lines = []
    for r in results:
        h = health.get(r.source.id)
        if r.status == "실패" and h:
            lines.append(f"[실패 {h.streak}회 연속] {r.source.name} ({r.source.id}) — "
                         f"{h.since}부터의 공고를 못 모았습니다. 사유: {r.error}")
        lines += [f"[주소 변경] {r.source.name} ({r.source.id}) — {w}" for w in r.warnings]
    return lines


ALERT_FOOTER = ("구독자 메일은 정상 발송했습니다. 실패한 출처는 복구되면 못 모은 기간(최대 60일)을 자동으로 다시 모읍니다. "
                "고치는 법: config/sources.yaml에서 해당 출처의 url을 새 주소로 바꾸거나, 더 쓰지 않을 출처는 "
                "enabled: false로 끕니다. 주소를 바꿔도 실패하면 화면 구조가 바뀐 것이니 Claude Code에 수정을 요청하세요.")


# ---------- 모드별 실행 ----------

def run_check_sources(cfg: AppConfig, env, today: date, out_dir: Path, *,
                      adapter_factory=create_adapter, mailer_factory=Mailer) -> int:
    since = today - timedelta(days=CHECK_DAYS)
    days_of = lambda s: API_CHECK_DAYS if s.type.startswith("api_") else CHECK_DAYS
    results = collect(cfg, env, since, today, include_disabled=True, adapter_factory=adapter_factory,
                      since_for=lambda s: today - timedelta(days=days_of(s)))
    table = source_table(results)
    log(table)
    write_summary(env, "## 출처 점검 결과\n\n" + table)
    color = {"성공": "#039855", "실패": "#d92d20", "꺼짐": "#667085"}
    rows = [{
        "id": r.source.id, "name": f"[{r.source.track}] {r.source.name}" if r.source.track else r.source.name,
        "status": r.status, "color": color[r.status],
        "count": len(r.notices), "calls": r.calls,
        "reason": " / ".join([r.error, *r.warnings] + (
            [f"최근 {days_of(r.source)}일간 새 글이 없습니다. 게시판이 다른 주소로 옮겨졌는지 확인해 보세요"]
            if r.status == "성공" and not r.notices else [])).strip(" /"),
        "recent": [f"{n.posted_at or ''} {n.title}" for n in
                   sorted(r.notices, key=lambda n: n.posted_at or date.min, reverse=True)[:3]],
    } for r in results]
    count = {s: sum(1 for r in results if r.status == s) for s in color}
    subject = f"[R&D Noti] 출처 점검 — 성공 {count['성공']}, 실패 {count['실패']}, 꺼짐 {count['꺼짐']}"
    html = render_named("check_sources.html", subject=subject, rows=rows, period=f"{since} ~ {today} (나라장터 API는 최근 {API_CHECK_DAYS}일)",
                        ok=count["성공"], failed=count["실패"], off=count["꺼짐"])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "check_sources.html").write_text(html, encoding="utf-8")
    if not send_admin(env, subject, html, table, mailer_factory):
        log("점검 결과 메일을 보내지 못했습니다. 위 표와 Summary 화면에서 결과를 확인하고, 메일 관련 Secrets를 점검하세요")
        return 1
    return 0


def _subscribers(cfg: AppConfig, env, dry_run: bool) -> list[Subscriber]:
    raw = env.get("SUBSCRIBERS_JSON", "")
    if not raw and dry_run:
        log("SUBSCRIBERS_JSON이 없어 '모든 분류를 구독하는 예시 구독자' 1명으로 미리보기를 만듭니다")
        raw = json.dumps([{"name": "미리보기_전체분류", "email": "preview@example.com", "active": True,
                           "categories": list(cfg.keywords.categories)}])
    subs, warnings = load_subscribers(raw, cfg.keywords, {s.id for s in cfg.sources}, cfg.groups)
    for w in warnings:
        log(f"주의: {w}")
    return subs


def run_weekly(cfg: AppConfig, env, today: date, out_dir: Path, state_dir: Path, *, dry_run: bool,
               adapter_factory=create_adapter, mailer_factory=Mailer) -> int:
    last_run = st.load_last_run(state_dir / "last_run.json")
    since, until = st.collection_window(last_run, today)
    log(f"수집 기간: {since} ~ {until} ({'미리보기' if dry_run else '정상'} 모드)")
    try:
        subs = _subscribers(cfg, env, dry_run)
    except SubscriberError as exc:
        log(f"구독자 목록 오류: {exc}")
        if not dry_run:
            admin_error(env, "구독자 목록 오류", [str(exc)], mailer_factory)
        return 1

    prev_health = st.load_health(state_dir / "source_health.json")
    results = collect(cfg, env, since, until, adapter_factory=adapter_factory, health=prev_health)
    health = next_health(results, prev_health)
    alerts = source_alert_lines(results, health)
    for line in alerts:
        log(f"출처 확인 필요: {line}")
    table = source_table(results)
    log(table)
    write_summary(env, f"## 출처별 수집 결과 ({since} ~ {until})\n\n" + table)
    failed = [r.source.name for r in results if r.status == "실패"]
    if results and len(failed) == len(results):
        log("모든 출처 수집 실패 → 구독자 메일을 보내지 않습니다")
        if not dry_run:
            admin_error(env, "모든 출처 수집 실패", [f"{r.source.name}: {r.error}" for r in results], mailer_factory)
        return 1

    portal = {s.id for s in cfg.sources if s.group == cfg.groups.portal_group}
    pool = [n for r in results for n in r.notices]
    title_only = {t.name for t in cfg.groups.tracks if t.dedupe_by_title}
    pool = dedupe_cross_source(drop_expired(dedupe_same_uid(pool), today), portal, title_only)
    seen = st.load_seen(state_dir / "seen.json")
    log(f"정리된 공고 {len(pool)}건, 구독자 {len(subs)}명")

    outgoing: list[tuple[Subscriber, list[Item], str, str, str]] = []
    for sub in subs:
        items = select_for_subscriber(pool, seen, sub, cfg.keywords, today)
        report = build_report(items, sub.categories, cfg.groups, today, since, until, failed, cfg.manual_sources)
        html, text = render(report)
        outgoing.append((sub, items, report.subject, html, text))
        log(f"- {sub.name}: {report.subject}")

    if dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, (sub, _, subject, html, text) in enumerate(outgoing, 1):
            stem = out_dir / f"{i:02d}_{_safe_name(sub.name)}"
            stem.with_suffix(".html").write_text(html, encoding="utf-8")
            stem.with_suffix(".txt").write_text(f"제목: {subject}\n\n{text}", encoding="utf-8")
        log(f"미리보기 파일 {len(outgoing)}개 저장: {out_dir}")
        return 0

    try:
        settings = MailSettings.from_env(env)
    except MailError as exc:
        log(str(exc))
        return 1
    send_failed: list[str] = []
    sent_items: list[Item] = []
    try:
        with mailer_factory(settings) as m:
            for sub, items, subject, html, text in outgoing:
                try:
                    m.send(build_message(settings, sub.email, subject, html, text))
                    sent_items += items
                    log(f"발송 완료: {sub.name}")
                except MailError as exc:
                    send_failed.append(f"{sub.name}: {exc}")
                    log(f"발송 실패: {sub.name}: {exc}")
    except MailError as exc:
        log(f"메일 서버 오류: {exc}")
        admin_error(env, "메일 발송 실패", [str(exc)], mailer_factory)
        return 1

    if send_failed:
        # 일부만 성공하면 상태를 갱신하지 않는다(다음 실행에서 같은 기간을 다시 보냄)
        admin_error(env, "일부 구독자 발송 실패", send_failed, mailer_factory)
        return 1

    new_seen = st.prune_seen(update_seen(seen, sent_items, today), today)
    st.save_seen(new_seen, state_dir / "seen.json")
    st.save_last_run(today, state_dir / "last_run.json")
    st.save_health(health, state_dir / "source_health.json")
    if alerts:
        admin_error(env, f"출처 확인 필요 {len(alerts)}건", alerts, mailer_factory,
                    subject=f"[R&D Noti] 출처 확인 필요 {len(alerts)}건", footer=ALERT_FOOTER)
    log(f"상태 갱신 완료: seen {len(new_seen)}건, 마지막 성공 실행일 {today}")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="R&D Noti 주간 공고 메일")
    p.add_argument("--mode", choices=["normal", "dry-run", "check_sources", "validate"],
                   default=os.environ.get("RUN_MODE") or "dry-run")
    p.add_argument("--today", help="기준일 YYYY-MM-DD (테스트용, 기본은 한국 시간 오늘)")
    p.add_argument("--out", default=str(ROOT / "output"))
    args = p.parse_args(argv)
    env = {k: v.strip() for k, v in os.environ.items()}   # Secrets 끝에 섞여 든 줄바꿈·빈칸 제거
    mask_in_actions(env)
    today = date.fromisoformat(args.today) if args.today else st.today_kst()
    try:
        cfg = load_config()
    except ConfigError as exc:
        # 설정 파일(config/*.yaml)을 잘못 고친 경우: 무엇이 틀렸는지 로그·요약·운영자 메일로 알린다
        log(f"설정 파일 오류: {exc}")
        write_summary(env, f"## 설정 파일 오류\n\n{exc}")
        if args.mode == "normal":
            admin_error(env, "설정 파일 오류", [str(exc), "config/ 폴더의 파일을 고친 뒤 다시 실행하세요."])
        return 1
    if args.mode == "validate":
        on = sum(1 for s in cfg.sources if s.enabled)
        words = sum(len(c.keywords) for c in cfg.keywords.categories.values())
        msg = (f"설정 파일 정상: 출처 {len(cfg.sources)}곳(켜짐 {on}), "
               f"분류 {len(cfg.keywords.categories)}개, 키워드 {words}개")
        log(msg)
        write_summary(env, "## " + msg)
        return 0
    out = Path(args.out)
    if args.mode == "check_sources":
        return run_check_sources(cfg, env, today, out)
    return run_weekly(cfg, env, today, out, st.STATE_DIR, dry_run=(args.mode == "dry-run"))


if __name__ == "__main__":
    sys.exit(main())

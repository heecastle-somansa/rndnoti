"""실행 진입점: 수집 → 정규화 → 매칭 → 리포트 → 발송 (개발요청서 2장).

    python -m rnd_noti.main --mode normal          정상: 메일 발송 + 상태 갱신
    python -m rnd_noti.main --mode dry-run         미리보기: HTML 파일만 저장, 발송·상태 갱신 없음
    python -m rnd_noti.main --mode check_sources   출처 점검: 출처별 수집 결과만 운영자에게

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
from .config import AppConfig, SourceConfig, load_config
from .mailer import MailError, Mailer, MailSettings, build_message
from .match import (Item, Subscriber, dedupe_cross_source, dedupe_same_uid, drop_expired,
                    select_for_subscriber, update_seen)
from .models import Notice
from .normalize import normalize, passes_title_filter
from .preview import SAMPLE
from .report import build_report, render, render_named
from .sources.base import SourceError
from .sources.http import mask
from .sources.registry import create_adapter
from .subscribers import SubscriberError, load_subscribers

ROOT = Path(__file__).resolve().parents[2]
CHECK_DAYS = 30            # 출처 점검 모드의 조회 기간
SECRET_KEYS = ("DATA_GO_KR_KEY", "SMTP_PASSWORD", "SMTP_USER", "ADMIN_EMAIL", "MAIL_REPLY_TO")


@dataclass
class SourceResult:
    source: SourceConfig
    status: str                      # 성공 | 실패 | 꺼짐
    notices: list[Notice] = field(default_factory=list)
    error: str = ""


def log(msg: str = "") -> None:
    print(msg, flush=True)


def _secrets(env: Mapping[str, str]) -> list[str]:
    values = [env.get(k, "") for k in SECRET_KEYS]
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
            include_disabled: bool = False, adapter_factory: Callable = create_adapter) -> list[SourceResult]:
    results = []
    secrets = _secrets(env)
    for src in cfg.sources:
        if not src.enabled:
            if include_disabled:
                results.append(SourceResult(src, "꺼짐", error=src.note))
            continue
        try:
            raw = adapter_factory(src, env).fetch(since, until)
            notices = [normalize(n, src, cfg.groups) for n in raw if passes_title_filter(n.title, src.title_filter)]
            results.append(SourceResult(src, "성공", notices))
        except SourceError as exc:
            results.append(SourceResult(src, "실패", error=mask(str(exc), secrets)))
        except Exception as exc:                              # 예상 못 한 오류도 그 출처만 실패 처리
            results.append(SourceResult(src, "실패", error=mask(f"{type(exc).__name__}: {exc}", secrets)[:300]))
    return results


def source_table(results: list[SourceResult]) -> str:
    lines = ["| 출처 | id | 결과 | 건수 | 비고 |", "| --- | --- | --- | --- | --- |"]
    for r in results:
        note = (r.error or r.source.note).replace("|", "/")
        lines.append(f"| {r.source.name} | {r.source.id} | {r.status} | {len(r.notices)} | {note} |")
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
    admin = env.get("ADMIN_EMAIL")
    if not admin:
        log("ADMIN_EMAIL이 없어 운영자 알림 메일을 보내지 않았습니다")
        return False
    try:
        settings = MailSettings.from_env(env)
        with mailer_factory(settings) as m:
            m.send(build_message(settings, admin, subject, html, text))
        log("운영자 알림 메일 발송 완료")
        return True
    except MailError as exc:
        log(f"운영자 알림 메일 발송 실패: {exc}")
        return False


def admin_error(env, title: str, lines: list[str], mailer_factory=Mailer) -> None:
    subject = f"[R&D Noti] 오류: {title}"
    html = render_named("admin_error.html", subject=subject, title=title, lines=lines)
    send_admin(env, subject, html, "\n".join([title, *lines]), mailer_factory)


# ---------- 모드별 실행 ----------

def run_check_sources(cfg: AppConfig, env, today: date, out_dir: Path, *,
                      adapter_factory=create_adapter, mailer_factory=Mailer) -> int:
    since = today - timedelta(days=CHECK_DAYS)
    results = collect(cfg, env, since, today, include_disabled=True, adapter_factory=adapter_factory)
    table = source_table(results)
    log(table)
    write_summary(env, "## 출처 점검 결과\n\n" + table)
    color = {"성공": "#039855", "실패": "#d92d20", "꺼짐": "#667085"}
    rows = [{
        "id": r.source.id, "name": r.source.name, "status": r.status, "color": color[r.status],
        "count": len(r.notices), "reason": r.error,
        "recent": [f"{n.posted_at or ''} {n.title}" for n in
                   sorted(r.notices, key=lambda n: n.posted_at or date.min, reverse=True)[:3]],
    } for r in results]
    count = {s: sum(1 for r in results if r.status == s) for s in color}
    subject = f"[R&D Noti] 출처 점검 — 성공 {count['성공']}, 실패 {count['실패']}, 꺼짐 {count['꺼짐']}"
    html = render_named("check_sources.html", subject=subject, rows=rows, period=f"{since} ~ {today}",
                        ok=count["성공"], failed=count["실패"], off=count["꺼짐"])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "check_sources.html").write_text(html, encoding="utf-8")
    send_admin(env, subject, html, table, mailer_factory)
    return 0


def _subscribers(cfg: AppConfig, env, dry_run: bool) -> list[Subscriber]:
    raw = env.get("SUBSCRIBERS_JSON", "")
    if not raw and dry_run:
        log("SUBSCRIBERS_JSON이 없어 미리보기용 예시 구독자(본인·지인)로 만듭니다")
        raw = json.dumps(json.loads(SAMPLE.read_text(encoding="utf-8"))["subscribers"])
    subs, warnings = load_subscribers(raw, cfg.keywords, {s.id for s in cfg.sources})
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

    results = collect(cfg, env, since, until, adapter_factory=adapter_factory)
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
    pool = dedupe_cross_source(drop_expired(dedupe_same_uid(pool), today), portal)
    seen = st.load_seen(state_dir / "seen.json")
    log(f"정리된 공고 {len(pool)}건, 구독자 {len(subs)}명")

    outgoing: list[tuple[Subscriber, list[Item], str, str, str]] = []
    for sub in subs:
        items = select_for_subscriber(pool, seen, sub, cfg.keywords, today)
        report = build_report(items, sub.categories, cfg.groups, today, since, until, failed)
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
    log(f"상태 갱신 완료: seen {len(new_seen)}건, 마지막 성공 실행일 {today}")
    return 0


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="R&D Noti 주간 공고 메일")
    p.add_argument("--mode", choices=["normal", "dry-run", "check_sources"],
                   default=os.environ.get("RUN_MODE") or "dry-run")
    p.add_argument("--today", help="기준일 YYYY-MM-DD (테스트용, 기본은 한국 시간 오늘)")
    p.add_argument("--out", default=str(ROOT / "output"))
    args = p.parse_args(argv)
    env = dict(os.environ)
    mask_in_actions(env)
    today = date.fromisoformat(args.today) if args.today else st.today_kst()
    cfg = load_config()
    out = Path(args.out)
    if args.mode == "check_sources":
        return run_check_sources(cfg, env, today, out)
    return run_weekly(cfg, env, today, out, st.STATE_DIR, dry_run=(args.mode == "dry-run"))


if __name__ == "__main__":
    sys.exit(main())

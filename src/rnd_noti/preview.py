"""샘플 데이터로 메일 미리보기 HTML을 만든다 (인터넷 접속·메일 발송 없음).

    python -m rnd_noti.preview            → output/preview_<구독자>.html / .txt

실제 수집·매칭과 같은 코드(normalize → match → report)를 그대로 거친다.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from .config import load_config
from .match import dedupe_cross_source, dedupe_same_uid, drop_expired, select_for_subscriber
from .models import Notice
from .normalize import normalize
from .report import build_report, render
from .state import SeenEntry
from .subscribers import load_subscribers

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "sample_notices.json"
SAMPLE_CONFIG = ROOT / "samples" / "config"      # 샘플 전용 고정 설정(운영 설정을 바꿔도 샘플은 그대로)


def build_sample(out_dir: Path) -> list[Path]:
    cfg = load_config(SAMPLE_CONFIG)
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    today, since = date.fromisoformat(data["today"]), date.fromisoformat(data["since"])
    sources = {s.id: s for s in cfg.sources}
    notices = []
    for raw in data["notices"]:
        src = sources[raw["source"]]
        raw = {"agency_group": src.group, "source_name": src.name, "url": src.url or "https://example.com/",
               "ministry": src.ministry, **raw}
        notices.append(normalize(Notice.from_dict(raw), src, cfg.groups))
    portal = {s.id for s in cfg.sources if s.group == cfg.groups.portal_group}
    title_only = {t.name for t in cfg.groups.tracks if t.dedupe_by_title}
    pool = dedupe_cross_source(drop_expired(dedupe_same_uid(notices), today), portal, title_only)
    seen = {e["notice"]["uid"]: SeenEntry(date.fromisoformat(e["first_sent"]), Notice.from_dict(e["notice"]))
            for e in data["seen"]}
    subs, _ = load_subscribers(json.dumps(data["subscribers"]), cfg.keywords, set(sources), cfg.groups)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for sub in subs:
        items = select_for_subscriber(pool, seen, sub, cfg.keywords, today)
        report = build_report(items, sub.categories, cfg.groups, today, since, today, data["failed_sources"])
        html, text = render(report)
        for ext, body in (("html", html), ("txt", f"제목: {report.subject}\n\n{text}")):
            path = out_dir / f"preview_{sub.name}.{ext}"
            path.write_text(body, encoding="utf-8")
            written.append(path)
    return written


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for p in build_sample(ROOT / "output"):
        print(p)

"""``applytex-watchlist``: manage the curated board watchlist and read the feed.

Works directly against the local SQLite store, so it runs with or without the
API. Examples::

    applytex-watchlist seed --domain robotics --domain autonomous_driving
    applytex-watchlist list
    applytex-watchlist refresh
    applytex-watchlist feed --since 24h --min-fit 60 --markdown auto
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from latex_resume.application_store import ApplicationStore
from latex_resume.job_models import JobPosting
from latex_resume.watchlist import WatchlistIngestor, load_seed, seed_entries_for_profile


def _store(path: str | None) -> ApplicationStore:
    db_path = path or os.environ.get("APPLYTEX_DB_PATH") or os.environ.get("SMARTJOBAPPLY_DB_PATH") or ".applytex/applytex.db"
    return ApplicationStore(Path(db_path))


def _profile(store: ApplicationStore, requested: str | None) -> str:
    return requested or store.get_active_profile_id()


def _since_iso(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower()
    unit, amount = cleaned[-1:], cleaned[:-1]
    if unit in {"h", "d", "w"} and amount.isdigit():
        delta = {"h": timedelta(hours=int(amount)), "d": timedelta(days=int(amount)), "w": timedelta(weeks=int(amount))}[unit]
        return (datetime.now(timezone.utc) - delta).isoformat()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _fit(job: JobPosting) -> str:
    return f"{job.fit_score:5.1f}" if job.fit_score is not None else "   — "


def render_feed_markdown(jobs: list[JobPosting], applied: set[str], *, since_label: str) -> str:
    lines = [f"# ApplyTeX job feed — {date.today().isoformat()}", "", f"{len(jobs)} matches (since {since_label}), best fit first.", ""]
    lines.append("| Fit | Company | Title | Location | Domain | Seen | Apply |")
    lines.append("|---:|---|---|---|---|---|---|")
    for job in jobs:
        seen = (job.first_seen_at or job.retrieved_at)[:10]
        flag = " ✅" if job.job_id in applied else ""
        tags = ", ".join(job.domain_tags[:2])
        lines.append(
            f"| {_fit(job).strip()} | {job.company}{flag} | {job.title} | {job.location or '—'} | {tags} | {seen} | [apply]({job.apply_url}) |"
        )
    lines.append("")
    lines.append("✅ = an application record already exists. Fit is the deterministic resume/JD score, not a hiring prediction.")
    return "\n".join(lines) + "\n"


def cmd_seed(args: argparse.Namespace) -> int:
    store = _store(args.db)
    profile_id = _profile(store, args.profile)
    entries = seed_entries_for_profile(profile_id, args.domain)
    if not entries:
        print("No seed entries match those domains. Available domains: " + ", ".join(sorted({t for e in load_seed() for t in e['domain_tags']})))
        return 1
    for entry in entries:
        store.upsert_watchlist_entry(entry)
    print(f"Added or refreshed {len(entries)} boards for profile '{profile_id}'.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = _store(args.db)
    profile_id = _profile(store, args.profile)
    entries = store.list_watchlist_entries(profile_id)
    if not entries:
        print(f"Watchlist for '{profile_id}' is empty. Run: applytex-watchlist seed")
        return 0
    print(f"{'on':<3} {'provider':<11} {'board':<22} {'company':<26} {'jobs':>5} {'match':>5}  checked / error")
    for entry in entries:
        checked = (entry.last_checked_at or "never")[:16]
        error = f"  {entry.last_error}" if entry.last_error else ""
        print(f"{'✓' if entry.enabled else '·':<3} {entry.provider.value:<11} {entry.board_token:<22} {entry.company[:26]:<26} {entry.last_job_count:>5} {entry.last_matched_count:>5}  {checked}{error}")
    print(f"\n{len(entries)} boards. Domains: " + ", ".join(sorted({t for e in entries for t in e.domain_tags})))
    return 0


def cmd_refresh(args: argparse.Namespace) -> int:
    store = _store(args.db)
    profile_id = _profile(store, args.profile)
    run = asyncio.run(WatchlistIngestor(store).refresh(profile_id, trigger="cli"))
    print(
        f"Refreshed {run.source_count} boards: fetched {run.fetched_jobs}, matched {run.matched_jobs}, "
        f"new {run.new_jobs}, updated {run.updated_jobs}, errors {len(run.errors)}."
    )
    for error in run.errors:
        print(f"  ! {error.provider.value}/{error.board_token}: {error.message}")
    return 0


def cmd_feed(args: argparse.Namespace) -> int:
    store = _store(args.db)
    profile_id = _profile(store, args.profile)
    since_iso = _since_iso(args.since)
    jobs = store.list_feed_jobs(profile_id, since=since_iso, min_fit=args.min_fit, domain_tags=args.domain, limit=args.limit)
    applied = {application.job_id for application in store.list_applications(limit=10_000, profile_id=profile_id)}
    if args.markdown:
        target = Path(args.markdown) if args.markdown != "auto" else Path(".applytex/feed") / f"{date.today().isoformat()}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_feed_markdown(jobs, applied, since_label=args.since or "always"), encoding="utf-8")
        print(f"Wrote {len(jobs)} matches to {target}")
        return 0
    if not jobs:
        print("No feed matches. Seed the watchlist and run `applytex-watchlist refresh` first.")
        return 0
    print(f"{'fit':>5}  {'seen':<10} {'company':<22} {'title':<48} {'location':<22} {'domain'}")
    for job in jobs:
        seen = (job.first_seen_at or job.retrieved_at)[:10]
        flag = "✅" if job.job_id in applied else "  "
        print(f"{_fit(job)}  {seen:<10} {flag}{job.company[:20]:<20} {job.title[:48]:<48} {(job.location or '—')[:22]:<22} {','.join(job.domain_tags[:2])}")
        if args.urls:
            print(f"       {job.apply_url}")
    print(f"\n{len(jobs)} matches. ✅ = application record exists.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="applytex-watchlist", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", help="SQLite path (default: APPLYTEX_DB_PATH or .applytex/applytex.db)")
    parser.add_argument("--profile", help="profile id (default: the active profile)")
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="add the bundled verified boards to the watchlist")
    seed.add_argument("--domain", action="append", help="only boards tagged with this domain (repeatable)")
    seed.set_defaults(func=cmd_seed)

    listing = sub.add_parser("list", help="show watchlist boards and their last refresh")
    listing.set_defaults(func=cmd_list)

    refresh = sub.add_parser("refresh", help="fetch every enabled board now")
    refresh.set_defaults(func=cmd_refresh)

    feed = sub.add_parser("feed", help="show ranked matches")
    feed.add_argument("--since", help="window like 24h, 3d, 2w, or an ISO timestamp")
    feed.add_argument("--min-fit", type=float, help="minimum fit score 0-100")
    feed.add_argument("--domain", action="append", help="only jobs from boards tagged with this domain")
    feed.add_argument("--limit", type=int, default=50)
    feed.add_argument("--urls", action="store_true", help="print apply URLs under each row")
    feed.add_argument("--markdown", help="write a digest to this path, or 'auto' for .applytex/feed/<date>.md")
    feed.set_defaults(func=cmd_feed)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

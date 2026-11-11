#!/usr/bin/env python3
"""
One-shot review check (run by GitHub Actions on a timer):

    scrape newest reviews -> store new ones in data/reviews.db
    -> mail new reviews rated below RATING_LIMIT -> mark them sent

Usage:
    python main.py                 # normal run
    python main.py --seed          # store current reviews without mailing (first setup)
    python main.py --dry-run       # scrape + show what would be mailed; no db write, no mail

The first run for a business with an empty db is treated as --seed automatically,
so existing old reviews are not mailed.

Config (env, all optional):
    QUERIES       Google search(es), separated by ';'   default: "Royal Migration Solutions Dubai reviews"
                  (use the full business name + city so Google shows the business panel)
    GL            Google results country code            default: ae
    LIMIT         newest reviews to check per query      default: 15
    RATING_LIMIT  alert when rating < this               default: 4
    DB_PATH       default: data/reviews.db
    HEADLESS      "1" to run Chromium headless (more CAPTCHAs)
    SMTP_USER / SMTP_PASS / MAIL_TO / SMTP_HOST / SMTP_PORT -> see mailer.py
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from db import ReviewDB
from mailer import send_alert
from scrape_reviews import save, scrape

log = logging.getLogger("review-alert")


def env(name: str, default: str) -> str:
    return os.environ.get(name) or default   # treat empty string (unset repo variable) as default


def gh_error(msg: str) -> None:
    """Also emit as a GitHub Actions annotation (visible on the run page without opening logs)."""
    if os.environ.get("GITHUB_ACTIONS"):
        print("::error::" + msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true", help="store reviews without sending mail")
    ap.add_argument("--dry-run", action="store_true", help="no db write, no mail")
    a = ap.parse_args()

    queries = [q.strip() for q in env("QUERIES", "Royal Migration Solutions Dubai reviews").split(";") if q.strip()]
    limit = int(env("LIMIT", "15"))
    rating_limit = int(env("RATING_LIMIT", "4"))
    headless = env("HEADLESS", "0") == "1"
    gl = env("GL", "ae")
    out_dir = Path("output")
    db = None if a.dry_run else ReviewDB(Path(env("DB_PATH", "data/reviews.db")))

    failed = 0
    for query in queries:
        try:
            reviews, total = asyncio.run(scrape(query, limit, "newest", headless, out_dir, gl))
            save(reviews, query, out_dir)   # json/csv snapshot -> uploaded as Actions artifact
        except Exception as e:
            log.error("%s: scrape failed: %s", query, e)
            gh_error(f"{query}: scrape failed: {e}")
            failed += 1
            continue
        if not reviews:
            log.error("%s: 0 reviews extracted (selectors changed or CAPTCHA?)", query)
            gh_error(f"{query}: 0 reviews extracted (selectors changed or CAPTCHA?)")
            failed += 1
            continue

        unrated = [r["review_id"] for r in reviews if r["rating"] is None]
        if unrated:
            log.warning("%s: %d review(s) with unreadable rating: %s", query, len(unrated), unrated)

        if a.dry_run:
            bad = [r for r in reviews if r["rating"] is not None and r["rating"] < rating_limit]
            log.info("%s (%s): %d scraped, %d below %d stars (dry run, not checked against db)",
                     query, total, len(reviews), len(bad), rating_limit)
            for r in bad:
                log.info("   %s* %s | %s | %s", r["rating"], r["name"], r["date_relative"], r["text"][:80])
            continue

        seed = a.seed or db.is_empty(query)
        new = db.insert_new(query, reviews, rating_limit, seed)
        log.info("%s (%s): %d scraped, %d new%s", query, total, len(reviews), len(new),
                 " (seeded, no mail)" if seed else "")

    if not a.dry_run:
        pending = db.pending()   # includes earlier reviews whose mail failed
        if pending:
            log.info("Mailing %d review(s) below %d stars", len(pending), rating_limit)
            try:
                send_alert(pending, rating_limit)
            except Exception as e:
                log.error("Mail failed, will retry next run: %s", e)
                gh_error(f"Mail failed, will retry next run: {e}")
                failed += 1
            else:
                db.mark_sent([r["review_id"] for r in pending],
                             datetime.now(timezone.utc).replace(microsecond=0).isoformat())
                log.info("Mail sent")
        else:
            log.info("No new reviews below %d stars", rating_limit)
    if db:
        db.close()
    return 1 if failed else 0   # red run in Actions so failures get noticed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())

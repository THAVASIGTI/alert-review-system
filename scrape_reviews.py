#!/usr/bin/env python3
"""
Google Search -> knowledge panel "N Google reviews" -> sort by Newest -> extract top N reviews.

Usage:
    python scrape_reviews.py --query "royal migration reviews" --limit 15
    python scrape_reviews.py --headless          # may trigger Google CAPTCHA
    python scrape_reviews.py --sort lowest       # most_relevant | newest | highest | lowest

Outputs output/reviews_<timestamp>.json and .csv (plus output/latest.json).
"""
import argparse
import asyncio
import csv
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

from playwright.async_api import async_playwright, TimeoutError as PWTimeout

SORT_KEYS = {"most_relevant": "1", "newest": "2", "highest": "3", "lowest": "4"}

# Selectors discovered from the live Google SERP reviews dialog (Nov 2026).
# Google obfuscates class names and changes them periodically -- update here if extraction breaks.
SEL = {
    "reviews_link": r"^[\d,.]+\s+Google reviews$",  # text of the clickable link in the side panel
    "sort_chip": "[role=radio][data-sort='{key}']",
    "card": "div[jsname='ShBeI'][data-id]",
}

EXTRACT_JS = r"""
(cards) => cards.map(e => {
  const q = s => e.querySelector(s);
  const txt = s => (q(s)?.innerText || '').trim();
  const stars = [...e.querySelectorAll('.h3PQJ svg path')];
  const filled = stars.filter(p => {
    const f = (p.getAttribute('fill') || '').toLowerCase();
    return f && !['#dadce0', '#e8eaed', '#9aa0a6', '#bdc1c6', 'none', 'transparent'].includes(f);
  }).length;
  const avatar = q('[role=img][aria-label]');
  const profile = q('a[href*="/maps/contrib/"]');
  return {
    review_id: e.getAttribute('data-id'),
    name: txt('.rhtdWc') || avatar?.getAttribute('aria-label') || '',
    reviewer_info: txt('.YE6B9b'),               // e.g. "Local Guide · 12 reviews" / "1 review"
    rating: stars.length ? filled : null,
    date_relative: txt('.m6Mr5d'),
    is_new: !!q('.M2wKwd'),
    text: txt('.d83Iyc'),
    profile_url: profile?.href || null,
  };
})
"""


def relative_to_iso(rel: str, now: datetime) -> str | None:
    """Convert '2 hours ago' / 'a week ago' / 'Edited 3 months ago' to an approximate ISO timestamp."""
    m = re.search(r"(a|an|\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago", rel or "", re.I)
    if not m:
        return None
    n = 1 if m.group(1).lower() in ("a", "an") else int(m.group(1))
    unit = m.group(2).lower()
    delta = {
        "second": timedelta(seconds=n), "minute": timedelta(minutes=n), "hour": timedelta(hours=n),
        "day": timedelta(days=n), "week": timedelta(weeks=n),
        "month": timedelta(days=30 * n), "year": timedelta(days=365 * n),
    }[unit]
    return (now - delta).replace(microsecond=0).isoformat()


def parse_reviewer_count(info: str) -> int | None:
    m = re.search(r"([\d,]+)\s+reviews?", info or "")
    return int(m.group(1).replace(",", "")) if m else None


async def scrape(query: str, limit: int, sort: str, headless: bool, out_dir: Path) -> tuple[list[dict], str]:
    """Returns (reviews, business_total_text e.g. '1,241 Google reviews')."""
    out_dir.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = await browser.new_context(
            locale="en-US",
            viewport={"width": 1366, "height": 900},
            user_agent=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        )
        page = await ctx.new_page()
        try:
            await page.goto(f"https://www.google.com/search?q={quote_plus(query)}&hl=en",
                            wait_until="domcontentloaded")

            # Cookie consent (EU / some regions)
            for label in ("Accept all", "I agree", "Reject all"):
                btn = page.get_by_role("button", name=label)
                if await btn.count():
                    await btn.first.click()
                    break

            if "/sorry/" in page.url:
                raise RuntimeError("Google returned a CAPTCHA page (/sorry). Try headed mode, "
                                   "a different IP, or run less often.")

            # 1) Click "1,241 Google reviews" (number is dynamic)
            link = page.get_by_text(re.compile(SEL["reviews_link"])).filter(visible=True).first
            await link.wait_for(timeout=15000)
            total_text = (await link.inner_text()).strip()
            await link.click()

            # 2) Click the "Newest" chip inside the popup
            chip = page.locator(SEL["sort_chip"].format(key=SORT_KEYS[sort])).filter(visible=True).first
            await chip.wait_for(timeout=15000)
            first_before = await page.locator(SEL["card"]).first.get_attribute("data-id")
            await chip.click()
            # wait until the list re-renders with the new sort order
            try:
                await page.wait_for_function(
                    """([sel, old]) => { const c = document.querySelector(sel);
                        return c && c.getAttribute('data-id') !== old; }""",
                    arg=[SEL["card"], first_before], timeout=10000)
            except PWTimeout:
                pass  # same first review in both orders -- fine
            await page.wait_for_timeout(1000)

            # 3) Scroll the popup until we have `limit` reviews
            cards = page.locator(SEL["card"])
            stale = 0
            while await cards.count() < limit and stale < 6:
                before = await cards.count()
                await cards.last.scroll_into_view_if_needed()
                await page.mouse.wheel(0, 3000)
                await page.wait_for_timeout(1500)
                stale = stale + 1 if await cards.count() == before else 0

            # 4) Expand truncated texts ("More" / "Read more") if present
            for name in ("More", "Read more"):
                more = page.locator(SEL["card"]).get_by_role("button", name=name, exact=True)
                for i in range(await more.count()):
                    try:
                        await more.nth(i).click(timeout=2000)
                    except PWTimeout:
                        pass

            raw = await page.locator(SEL["card"]).evaluate_all(EXTRACT_JS)
        except Exception as e:
            await page.screenshot(path=str(out_dir / "error.png"), full_page=False)
            # where we ended up: helps tell CAPTCHA / consent page / layout change apart
            try:
                body = (await page.locator("body").inner_text(timeout=3000))[:300]
            except Exception:
                body = ""
            title = await page.title()
            raise RuntimeError(f"{type(e).__name__}: {str(e).splitlines()[0][:200]} | url={page.url[:150]} "
                               f"| title={title!r} | page={' '.join(body.split())!r}") from e
        finally:
            await browser.close()

    now = datetime.now(timezone.utc)
    seen, reviews = set(), []
    for r in raw:
        if r["review_id"] in seen:
            continue
        seen.add(r["review_id"])
        r["reviewer_review_count"] = parse_reviewer_count(r["reviewer_info"])
        r["date_estimated_utc"] = relative_to_iso(r["date_relative"], now)
        r["scraped_at_utc"] = now.replace(microsecond=0).isoformat()
        reviews.append(r)
        if len(reviews) >= limit:
            break

    print(f"Business total: {total_text}; extracted {len(reviews)} reviews (sort={sort})", file=sys.stderr)
    return reviews, total_text


def save(reviews: list[dict], query: str, out_dir: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {"query": query, "count": len(reviews), "reviews": reviews}
    # never overwrite a good latest.json with an empty run
    targets = [out_dir / f"reviews_{stamp}.json"] + ([out_dir / "latest.json"] if reviews else [])
    for path in targets:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    if reviews:
        with open(out_dir / f"reviews_{stamp}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(reviews[0].keys()))
            w.writeheader()
            w.writerows(reviews)
    print(f"Saved to {out_dir}/reviews_{stamp}.json/.csv", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default="royal migration reviews")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--sort", choices=SORT_KEYS, default="newest")
    ap.add_argument("--headless", action="store_true", help="headless (Google is more likely to CAPTCHA)")
    ap.add_argument("--out", default="output")
    a = ap.parse_args()

    out = Path(a.out)
    reviews, _ = asyncio.run(scrape(a.query, a.limit, a.sort, a.headless, out))
    save(reviews, a.query, out)
    if len(reviews) == 0:
        sys.exit(2)


if __name__ == "__main__":
    main()

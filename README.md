# alert-review-system

Checks a business's newest Google reviews every 5 minutes (GitHub may delay or skip scheduled runs) on GitHub Actions and emails new reviews rated below 4 stars.

```
cron */5        -> scrape newest reviews (Playwright + xvfb)
          -> store new ones in data/reviews.db (committed back to the repo)
          -> mail new reviews with rating < RATING_LIMIT -> mark them sent
```

## Files

| File | Purpose |
|---|---|
| `main.py` | One-shot run: scrape, then store in the db, then mail. `--seed` stores reviews without mailing. `--dry-run` writes nothing and sends nothing. `--test-mail` sends one sample alert to check the mail settings. |
| `scrape_reviews.py` | Google search, then the "N Google reviews" panel, sorted by Newest. Can also be run on its own. |
| `db.py` | SQLite store. `alert_state` is `none` / `pending` / `sent` / `seeded`. |
| `mailer.py` | HTML alert mail over SMTP (Gmail by default). |
| `.github/workflows/review-alert.yml` | 15-minute schedule, plus a manual run with a mode choice. |
| `.github/workflows/keepalive.yml` | Makes an empty commit after 50 idle days so GitHub doesn't disable the schedule. |

## Setup

1. **Settings → Actions → General → Workflow permissions →** select **Read and write permissions**.
2. **Settings → Secrets and variables → Actions → Secrets:**
   - `SMTP_USER`: the Gmail address that sends the alerts.
   - `SMTP_PASS`: a Gmail [App Password](https://myaccount.google.com/apppasswords), not the account password. 2-Step Verification must be on.
   - `MAIL_TO`: recipients, comma-separated.
3. **Variables** (optional):
   - `QUERIES`: Google searches separated by `;`, e.g. `Royal Migration Solutions Dubai reviews;Royal Real Estate Dubai reviews`. Use the full business name and city: GitHub's runners are in the US, and for a short query Google shows no business panel there.
   - `LIMIT`: how many of the newest reviews to check (default 15).
   - `RATING_LIMIT`: alert when the rating is below this (default 4).
   - `GL`: Google results country code (default `ae`).
   - `SCHEDULE_MODE`: what the 15-minute scheduled runs do: `normal` (default; store and mail) or `seed` (store only, **no alert mail**). Manual runs use the mode you pick.
4. **Actions → Review alert → Run workflow** with mode `seed`. The first run for each query also seeds automatically, so existing reviews are never mailed.

## Behaviour

- Only reviews not already in the db are considered, so each review is mailed at most once.
- A review is marked sent only after the mail succeeds. If SMTP fails, the run goes red and the next run retries.
- If scraping fails (CAPTCHA, or Google changed its page layout), the run goes red. The screenshot `output/error.png` is uploaded as a run artifact.
- This is a public repo, so `data/reviews.db` (reviewer names, review text) is publicly visible.

## Local run

```bash
pip install -r requirements.txt && python -m playwright install chromium
python main.py --dry-run
SMTP_USER=... SMTP_PASS=... MAIL_TO=... python main.py
```

# alert-review-system

Checks a business's newest Google reviews every 15 minutes on GitHub Actions and emails any **new review rated below 4 stars**.

```
cron-job.org (every 15 min) ──POST dispatch API──┐
GitHub schedule (*/5, backup, unreliable) ───────┤
Run workflow button (manual) ────────────────────┘
        │
        ▼
Review alert workflow (Playwright Docker image)
  -> scrape newest reviews (Google search, sorted by Newest)
  -> store new ones in data/reviews.db (committed back to the repo)
  -> mail new reviews with rating < RATING_LIMIT -> mark them sent
```

## Files

| File | Purpose |
|---|---|
| `main.py` | One-shot run: scrape, then store in the db, then mail. Flags: `--seed`, `--dry-run`, `--test-mail` (see [Modes](#modes)). |
| `scrape_reviews.py` | Google search, then the "N Google reviews" panel, sorted by Newest. Can also be run on its own. |
| `db.py` | SQLite store. `alert_state` is `none` / `pending` / `sent` / `seeded`. |
| `mailer.py` | HTML alert mail over SMTP (Gmail by default). Raises on failure so unsent reviews are retried. |
| `.github/workflows/review-alert.yml` | The check. Runs in `mcr.microsoft.com/playwright/python:v1.58.0-noble` (must match `playwright==` in `requirements.txt`). |
| `.github/workflows/keepalive.yml` | Daily: makes an empty commit after 50 idle days so GitHub doesn't disable scheduled workflows. |
| `data/reviews.db` | Every review seen so far, committed by the workflow. |

## Modes

| Mode | What it does | Use it for |
|---|---|---|
| **normal** | read reviews → store new ones → **email** any below 4 stars | the every-15-minute job |
| **seed** | read and store, **no email** | first setup only |
| **dry-run** | read and show results; **no db, no email** | checking the scraper |
| **test-mail** | send one `[TEST]` email; no reading, no db | checking the Gmail settings |

Only **normal** sends alerts. Never run **seed** on a timer: it stores bad reviews as already seen, and they are never emailed later.

### Which mode a run uses

```yaml
MODE: ${{ inputs.mode || vars.SCHEDULE_MODE || 'normal' }}
```

| Started by | Mode used |
|---|---|
| **cron-job.org** with body `{"ref":"main"}` | **normal** (the `mode` input's default) |
| cron-job.org with body `{"ref":"main","inputs":{"mode":"dry-run"}}` | the mode given in `inputs` |
| GitHub's own schedule | repo variable `SCHEDULE_MODE`, otherwise **normal** |
| **Run workflow** button | whatever you pick in the dropdown |

`SCHEDULE_MODE` only affects GitHub's own schedule. cron-job.org runs always send a mode, so the variable doesn't apply to them.

Each run's log starts with a line such as `Mode: normal (workflow_dispatch)`.

## How runs are started

### cron-job.org (main trigger, every 15 minutes)

GitHub's own scheduler barely runs this workflow. Most `schedule` slots are dropped, and the rest start hours late (see [Notes](#notes)). So [cron-job.org](https://cron-job.org) calls GitHub's [workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event) every 15 minutes. That is the same as clicking **Run workflow**, so these runs show as **"Manually triggered"**.

**1. Create a GitHub token** (only for this repo):
1. Open https://github.com/settings/personal-access-tokens/new: **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Fill in:
   - Token name: `cron-job-review-alert`
   - Resource owner: `THAVASIGTI`
   - Expiration: the longest allowed (1 year)
   - Repository access: **Only select repositories** → `alert-review-system`
   - Permissions → Repository permissions → **Actions: Read and write** (Metadata read-only is added automatically)
3. Click **Generate token** and copy it (`github_pat_…`). It is shown only once. Never commit it or paste it anywhere public.

**2. Create the cron-job.org job** (Dashboard → Cronjobs → Create cronjob):

| Tab | Field | Value |
|---|---|---|
| COMMON | Title | `Review alert` |
| COMMON | URL | `https://api.github.com/repos/THAVASIGTI/alert-review-system/actions/workflows/review-alert.yml/dispatches` |
| COMMON | Execution schedule | **Every 15 minutes** |
| ADVANCED | Time zone | `Asia/Kolkata` |
| ADVANCED | Request method | **POST** |
| ADVANCED | Header `Authorization` | `Bearer github_pat_…` |
| ADVANCED | Header `Accept` | `application/vnd.github+json` |
| ADVANCED | Header `Content-Type` | `application/json` |
| ADVANCED | Header `X-GitHub-Api-Version` | `2022-11-28` |
| ADVANCED | Request body | `{"ref":"main"}` |
| NOTIFICATIONS | Notify on failure | on (warns when the token expires) |

**3. Test it:** open the job → **TEST RUN**. The response should be **204 No Content** (or **200** on newer API versions), and a new run should appear under **Actions → Review alert** within about a minute.

You can also test the token from a terminal:
```bash
read -sp "Token: " T; echo
curl -i -X POST -H "Authorization: Bearer $T" -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/THAVASIGTI/alert-review-system/actions/workflows/review-alert.yml/dispatches \
  -d '{"ref":"main"}'
```

| Response | Cause |
|---|---|
| `204` / `200` | success |
| `401 Bad credentials` | token copied wrong, or `Bearer ` missing in front of it |
| `403 Resource not accessible` | token lacks **Actions: Read and write**, or the repo isn't selected |
| `404 Not Found` | wrong URL, or the token can't see the repo |
| `422 Unprocessable` | wrong body; it must be `{"ref":"main"}` |

**Renew the token before it expires** (about a year after creation) and update the `Authorization` header in cron-job.org.

### GitHub schedule (backup)

`review-alert.yml` also has `cron: "*/5 * * * *"`. GitHub runs it only occasionally. It does no harm alongside cron-job.org: runs queue instead of overlapping (`concurrency: review-alert`), and the db stops any review from being emailed twice.

### Run workflow button (manual)

**Actions → Review alert → Run workflow**, then pick branch `main` and a mode.

## Setup (first time)

1. **Settings → Actions → General → Workflow permissions →** select **Read and write permissions**.
2. **Settings → Secrets and variables → Actions → Secrets → New repository secret.** Use *Repository* secrets, not *Environment* secrets: the workflow has no `environment:`, so environment secrets would arrive empty.
   - `SMTP_USER`: the full Gmail address that sends the alerts.
   - `SMTP_PASS`: a Gmail [App Password](https://myaccount.google.com/apppasswords) (16 characters, spaces are fine), not the normal password. 2-Step Verification must be on.
   - `MAIL_TO`: recipients, comma-separated.
3. **Variables** (optional, **New repository variable**):
   - `QUERIES`: Google searches separated by `;`, e.g. `Royal Migration Solutions Dubai reviews;Royal Real Estate Dubai reviews`. Use the full business name and city: GitHub's runners are in the US, and for a short query Google shows no business panel there.
   - `LIMIT`: how many of the newest reviews to check per run (default 15). Raise it (e.g. 50) if runs are far apart.
   - `RATING_LIMIT`: alert when the rating is below this (default 4).
   - `GL`: Google results country code (default `ae`).
   - `SCHEDULE_MODE`: mode for GitHub's own scheduled runs (default `normal`).
4. **Run workflow** with mode **`seed`** once. The first run for each query also seeds automatically, so existing reviews are never mailed.
5. **Run workflow** with mode **`test-mail`** and check the inbox (and spam) for `[TEST] Negative review alert…`.
6. Set up [cron-job.org](#cron-joborg-main-trigger-every-15-minutes).

## Checking runs and logs

- **All runs:** https://github.com/THAVASIGTI/alert-review-system/actions/workflows/review-alert.yml
- **Only GitHub-scheduled runs:** add the filter `event:schedule`, or open **Event ▾ → schedule** above the run list.
- **A run's log:** click the run → job **check** → step **Check reviews**. Typical lines:
  ```
  Mode: normal (workflow_dispatch)
  Business total: 1,242 Google reviews; extracted 15 reviews (sort=newest)
  INFO Royal Migration Solutions Dubai reviews (1,242 Google reviews): 15 scraped, 2 new
  INFO Mailing 1 review(s) below 4 stars
  INFO Mail sent
  ```
  or `INFO No new reviews below 4 stars` when nothing needs an alert.
- **Errors** also show as red annotations on the run summary page, without opening the log.
- **Artifacts** (bottom of the run page, kept 7 days): JSON/CSV of the reviews read, plus `error.png` when the scrape failed.
- **From a terminal** (GitHub CLI):
  ```bash
  gh run list -R THAVASIGTI/alert-review-system --limit 10
  gh run view <run-id> -R THAVASIGTI/alert-review-system --log
  gh api "repos/THAVASIGTI/alert-review-system/actions/runs?event=schedule&per_page=10" \
    --jq '.workflow_runs[] | "\(.created_at) \(.name) \(.conclusion)"'
  ```
- **The db:**
  ```bash
  git pull
  sqlite3 -header data/reviews.db \
    "select first_seen_utc, rating, alert_state, name from reviews order by first_seen_utc desc limit 20;"
  ```

## Behaviour

- Only reviews not already in the db are considered, so each review is mailed at most once.
- A review is marked sent only after the mail succeeds. If SMTP fails, the run goes red and the next run retries.
- If scraping fails (CAPTCHA, or Google changed its page layout), the run goes red, with the page URL and title in the error note and `error.png` in the artifacts.
- The db is committed only when it changes (a new review was stored or mailed).
- This is a public repo, so `data/reviews.db` (reviewer names, review text) is publicly visible. Secrets stay hidden.

## Troubleshooting

| Error note on the run | Cause / fix |
|---|---|
| `empty/missing secret(s): …` | Secret missing or misnamed. Names must be exactly `SMTP_USER`, `SMTP_PASS`, `MAIL_TO`, as *Repository* secrets. |
| `SMTP login rejected …` | Gmail refused the login. Gmail usually just drops the connection (`Connection unexpectedly closed`) instead of saying "wrong password". Use the full address and a fresh App Password. |
| `scrape failed: … url=…/sorry/…` | Google CAPTCHA. Usually temporary. |
| `scrape failed: …` with a normal results page | Google didn't show the business panel. Check `QUERIES` uses the full name and city, and `GL`. |
| `0 reviews extracted` | Google changed its page; selectors in `scrape_reviews.py` (`SEL`, `EXTRACT_JS`) need updating. |
| Fails at installing packages | Usually a temporary download problem. Run again. |

## Local run

```bash
pip install -r requirements.txt && python -m playwright install chromium
python main.py --dry-run
SMTP_USER=... SMTP_PASS=... MAIL_TO=... python main.py --test-mail
SMTP_USER=... SMTP_PASS=... MAIL_TO=... python main.py
```

## Notes

- **GitHub's scheduler is unreliable for this repo.** Checked against [GitHub's docs](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule): the cron format is valid, the workflow is on the default branch and active. Still, most `schedule` runs never start, the ones that do start hours late, and each edit to the `cron:` line seemed to restart a wait of many hours. Every format tried (`*/5`, `*/10`, off-peak minutes, hourly, two entries, `timezone:`) behaved the same. That is why cron-job.org is the main trigger. Avoid editing the `cron:` line without a reason.
- **Keepalive** relies on GitHub's scheduler too (daily, 00:00 UTC = 5:30 AM IST, often late). Runs started by cron-job.org don't count as repo activity for the 60-day rule; only commits do. New reviews committed to `data/reviews.db` keep the repo active.
- **GitHub's terms:** GitHub's [Actions terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions) say Actions is for developing and testing software, not for running an always-on service. Running this check every 15 minutes is outside that, and GitHub may restrict Actions on the repo. The fallback is running `main.py` with a cron job on your own machine.

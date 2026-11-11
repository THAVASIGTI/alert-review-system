"""SQLite store for seen reviews (data/reviews.db, committed back to the repo by GitHub Actions).

alert_state:
    none     rating >= limit (or unknown), nothing to send
    pending  rating < limit, mail not sent yet (retried on every run until it succeeds)
    sent     mail sent
    seeded   stored by the first/seed run, never mailed
"""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    review_id       TEXT PRIMARY KEY,
    business        TEXT NOT NULL,
    name            TEXT,
    rating          INTEGER,
    text            TEXT,
    date_relative   TEXT,
    date_estimated  TEXT,
    reviewer_info   TEXT,
    profile_url     TEXT,
    first_seen_utc  TEXT NOT NULL,
    alert_state     TEXT NOT NULL DEFAULT 'none',
    alerted_utc     TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_state ON reviews(alert_state);
"""


class ReviewDB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def is_empty(self, business: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM reviews WHERE business = ? LIMIT 1", (business,)).fetchone() is None

    def insert_new(self, business: str, reviews: list[dict], rating_limit: int, seed: bool) -> list[dict]:
        """Insert reviews not seen before. Returns the newly inserted ones."""
        new = []
        with self.conn:
            for r in reviews:
                if self.conn.execute("SELECT 1 FROM reviews WHERE review_id = ?", (r["review_id"],)).fetchone():
                    continue
                if seed:
                    state = "seeded"
                elif r["rating"] is not None and r["rating"] < rating_limit:
                    state = "pending"
                else:
                    state = "none"
                self.conn.execute(
                    """INSERT INTO reviews (review_id, business, name, rating, text, date_relative,
                           date_estimated, reviewer_info, profile_url, first_seen_utc, alert_state)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (r["review_id"], business, r["name"], r["rating"], r["text"], r["date_relative"],
                     r["date_estimated_utc"], r["reviewer_info"], r["profile_url"], r["scraped_at_utc"], state))
                new.append({**r, "business": business, "alert_state": state})
        return new

    def pending(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM reviews WHERE alert_state = 'pending' ORDER BY business, first_seen_utc").fetchall()
        return [dict(r) for r in rows]

    def mark_sent(self, review_ids: list[str], when: str) -> None:
        with self.conn:
            self.conn.executemany(
                "UPDATE reviews SET alert_state = 'sent', alerted_utc = ? WHERE review_id = ?",
                [(when, rid) for rid in review_ids])

    def close(self) -> None:
        self.conn.close()

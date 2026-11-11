"""HTML alert mail over SMTP (Gmail by default). Raises on failure so the caller never marks unsent reviews as sent.

Env:
    SMTP_USER   sender address (Gmail: the account address)
    SMTP_PASS   password (Gmail: an App Password, not the account password)
    MAIL_TO     comma-separated recipients
    SMTP_HOST   default smtp.gmail.com
    SMTP_PORT   default 587 (STARTTLS)
"""
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from itertools import groupby


def build_html(reviews: list[dict], rating_limit: int, now: str) -> str:
    sections = []
    for business, items in groupby(sorted(reviews, key=lambda r: r["business"]), key=lambda r: r["business"]):
        rows = "".join(
            f"""<tr>
                  <td>{escape(r['name'] or '')}</td>
                  <td style="color:#c00;font-weight:bold">{r['rating']} &#9733;</td>
                  <td>{escape(r['date_relative'] or '')}</td>
                  <td style="max-width:420px">{escape(r['text'] or '(no text)').replace(chr(10), '<br/>')}</td>
                  <td>{f'<a href="{escape(r["profile_url"], quote=True)}">reviewer</a>' if r.get('profile_url') else ''}</td>
                </tr>"""
            for r in items)
        sections.append(f"""
            <h3 style="color:#1a7f37"><u>{escape(business.upper())}</u></h3>
            <table cellpadding="4" style="border-collapse:collapse" border="1">
              <tr><th>Name</th><th>Rating</th><th>Posted</th><th>Review</th><th>Link</th></tr>
              {rows}
            </table><br/>""")

    return f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;font-size:14px">
  <p>Date (UTC): <b>{now}</b><br/>
     Criteria: newest Google reviews, new since last check, rating below <b>{rating_limit}</b> stars.</p>
  {''.join(sections)}
  <p style="color:#555">Automated check via GitHub Actions.<br/><br/>Regards,<br/><b>TMT Devs</b></p>
</body></html>"""


def send_alert(reviews: list[dict], rating_limit: int, subject_prefix: str = "") -> None:
    user, pwd = os.environ["SMTP_USER"], os.environ["SMTP_PASS"]
    recipients = [a.strip() for a in os.environ["MAIL_TO"].split(",") if a.strip()]
    if not recipients:
        raise RuntimeError("MAIL_TO is empty")
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or 587)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{subject_prefix}Negative review alert: {len(reviews)} new review(s) below {rating_limit} stars | {now} UTC"
    msg["From"] = user
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(build_html(reviews, rating_limit, now), "html", "utf-8"))

    with smtplib.SMTP(host, port, timeout=60) as server:
        server.starttls()
        server.login(user, pwd)
        server.sendmail(user, recipients, msg.as_string())

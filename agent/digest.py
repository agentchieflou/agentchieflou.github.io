"""Builds and sends the daily email digest.

The digest is the ONLY outbound communication this system produces, and it
goes solely to the owner's own address. Applying to anything remains a human
decision made from the links in the email.
"""
import datetime as dt
import html
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

from applied import SUBJECT_TAG
from config import (EMAIL_TO, GMAIL_ADDRESS, GMAIL_APP_PASSWORD,
                    MIN_SALARY_USD, STATE_DIR, TOP_N_DIGEST)
from util import log


def combined_score(s):
    """Confidence-weighted score used for final ordering."""
    return s["match_score"] * (0.6 + 0.4 * s["confidence"])


def pick_top(jobs, scores, n=TOP_N_DIGEST, exclude=()):
    """Top-n scored jobs, skipping ids in `exclude` (already applied)."""
    scored = [(j, scores[j["id"]]) for j in jobs
              if j["id"] in scores and j["id"] not in exclude]
    scored.sort(key=lambda js: -combined_score(js[1]))
    return scored[:n]


def _mark_applied_link(j):
    body = f"applied {j['id']}\n({j['title']} @ {j['company']})"
    return (f"mailto:{EMAIL_TO}?subject={quote(SUBJECT_TAG)}"
            f"&body={quote(body)}")


def build_html(top, stats):
    e = html.escape
    rows = []
    new_ids = set(stats.get("new_ids") or [])
    for i, (j, s) in enumerate(top, 1):
        missing = ", ".join(s["missing_skills"]) or "none identified"
        salary = f" · {e(j['salary'])}" if j.get("salary") else ""
        new_badge = ("""<span style="background:#0a7d33;color:#fff;border-radius:4px;
          font-size:11px;padding:1px 6px;margin-left:6px;">NEW</span>"""
                     if j["id"] in new_ids else "")
        rows.append(f"""
        <div style="border:1px solid #ddd;border-radius:10px;padding:16px;margin:12px 0;">
          <div style="font-size:16px;font-weight:bold;">{i}. {e(j['title'])} — {e(j['company'])}{new_badge}</div>
          <div style="color:#555;font-size:13px;margin:4px 0;">{e(j['location'])}{salary}
            · Match <b>{s['match_score']}/100</b> · Confidence {round(s['confidence'] * 100)}%
            · via {e(j['source'])}</div>
          <div style="font-size:14px;margin:8px 0;">{e(s['why'])}</div>
          <div style="font-size:13px;color:#555;">Missing skills: {e(missing)}</div>
          <div style="margin-top:8px;"><a href="{e(j['url'])}">Apply / view posting →</a>
            &nbsp;·&nbsp; <a href="{e(_mark_applied_link(j))}" style="color:#0a7d33;">✓ Mark applied</a></div>
        </div>""")

    suggestions = sorted({t for _, s in top for t in s.get("resume_suggestions", [])})
    sugg_html = ""
    if suggestions:
        items = "".join(f"<li>{e(t)}</li>" for t in suggestions)
        sugg_html = f"<h3 style='margin-top:24px;'>Resume suggestions</h3><ul>{items}</ul>"

    profile_note = f"<li>Skill profile: {e(stats['profile_note'])}</li>" if stats.get("profile_note") else ""
    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif;max-width:680px;margin:auto;color:#111;">
      <h2>Career Agent — Top {len(top)} matches for {stats['date']}</h2>
      <p style="font-size:12px;color:#777;margin-top:-8px;">Filters: fully remote ·
        stated salary required, min ${MIN_SALARY_USD:,} · already-applied jobs excluded</p>
      {''.join(rows) or '<p>No scored matches yet — the next runs will fill this in.</p>'}
      {sugg_html}
      <h3 style="margin-top:24px;">Run summary</h3>
      <ul style="font-size:13px;color:#444;">
        <li>{stats['evaluated']} jobs evaluated across {stats['sources']} sources</li>
        <li>{stats['new']} newly discovered postings</li>
        <li>{stats['expired']} expired postings removed</li>
        <li>{stats.get('applied_total', 0)} jobs marked applied to date ·
            {stats.get('discovered_total', 0)} qualifying jobs discovered all-time</li>
        {profile_note}
      </ul>
      <p style="font-size:11px;color:#999;margin-top:24px;">
        "✓ Mark applied" composes an email to yourself — send it as-is and the next
        run records the application and stops resurfacing that job.
        This digest is informational only — nothing was applied to on your behalf.
        Job data includes listings from Remotive, RemoteOK (<a href="https://remoteok.com">remoteok.com</a>),
        Arbeitnow, Hacker News, Adzuna, USAJobs and doomersareretardedcommunists.com.
      </p>
    </div>"""


def send_digest(top, stats, dry_run=False):
    body = build_html(top, stats)
    subject = f"Career Agent · {len(top)} matches · {stats['date']}"

    preview = STATE_DIR / "last_digest.html"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_text(body, encoding="utf-8")

    if dry_run:
        log.info("dry-run: digest written to %s (not emailed)", preview)
        return False
    if not GMAIL_APP_PASSWORD:
        log.warning("GMAIL_APP_PASSWORD not set - digest written to %s but not emailed", preview)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText("Your email client does not support HTML.", "plain"))
    msg.attach(MIMEText(body, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [EMAIL_TO], msg.as_string())
    except Exception as e:
        # A bad SMTP secret must not kill the run — state/graph still commit,
        # and the digest is preserved in the state dir.
        log.warning("digest email failed (check GMAIL_APP_PASSWORD is a Google "
                    "app password, not your account password): %s", e)
        return False
    log.info("digest emailed to %s", EMAIL_TO)
    return True

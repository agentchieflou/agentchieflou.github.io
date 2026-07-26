"""Builds and sends the daily email digest.

The digest is the ONLY outbound communication this system produces, and it
goes solely to the owner's own address. Applying to anything remains a human
decision made from the links in the email.

Markup constraints, because this renders in a mail client and not a browser:
tables for layout (Outlook has no flexbox or grid), styles inline on the
elements that carry them, no external stylesheets or webfonts, and a real
plain-text alternative rather than a "your client doesn't support HTML"
stub. The one <style> block carries only the dark-mode overrides and the
narrow-screen tweak — clients that strip it still get the full light-mode
layout from the inline styles.
"""
import datetime as dt
import html
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

from applied import SUBJECT_TAG
from config import (EMAIL_TO, GMAIL_ADDRESS, GMAIL_APP_PASSWORD,
                    MIN_SALARY_USD, NO_SALARY_MAX_AGE_DAYS,
                    NO_SALARY_MAX_IN_DIGEST, NO_SALARY_MIN_CONFIDENCE,
                    NO_SALARY_MIN_SCORE, NO_SALARY_SENIORITY_FITS, STATE_DIR,
                    TOP_N_DIGEST)
from rejected import SUBJECT_TAG as REJECTED_SUBJECT_TAG
from util import log

FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',"
        "Arial,sans-serif")

# Score bands -> (background, text) for the match pill. Muted enough to read
# as information rather than an alert.
_BANDS = [(85, "#e7f4ea", "#146c2e"), (70, "#fef4e3", "#8a5300"), (0, "#eef0f2", "#4a5057")]


def combined_score(s):
    """Confidence-weighted score used for final ordering."""
    return s["match_score"] * (0.6 + 0.4 * s["confidence"])


def no_salary_qualifies(s):
    """The extra bar a posting with undisclosed pay has to clear.

    Compensation is the one unknown about these, so nothing else may be. The
    role has to be one the owner could realistically get and a genuine step
    up — not a long shot, and not a lateral move that merely ranks well.
    Heuristic scores carry no seniority verdict, so they never qualify: with
    no LLM judgment available this fails closed.
    """
    return (s.get("seniority_fit") in NO_SALARY_SENIORITY_FITS
            and s.get("match_score", 0) >= NO_SALARY_MIN_SCORE
            and s.get("confidence", 0) >= NO_SALARY_MIN_CONFIDENCE)


def pick_top(jobs, scores, n=TOP_N_DIGEST, exclude=()):
    """Top-n scored jobs, skipping ids in `exclude` (already applied).

    Postings with undisclosed pay are capped at NO_SALARY_MAX_IN_DIGEST and
    must clear no_salary_qualifies(); they never crowd out a role that
    states what it pays.
    """
    scored = [(j, scores[j["id"]]) for j in jobs
              if j["id"] in scores and j["id"] not in exclude]
    scored.sort(key=lambda js: -combined_score(js[1]))

    picked, undisclosed = [], 0
    for j, s in scored:
        if len(picked) >= n:
            break
        if j.get("no_salary"):
            if undisclosed >= NO_SALARY_MAX_IN_DIGEST or not no_salary_qualifies(s):
                continue
            undisclosed += 1
        picked.append((j, s))
    if undisclosed:
        log.info("digest: %d of %d entries are employer-direct roles with "
                 "undisclosed pay", undisclosed, len(picked))
    return picked


def _mailto(j, tag, verb):
    body = f"{verb} {j['id']}\n({j['title']} @ {j['company']})"
    return f"mailto:{EMAIL_TO}?subject={quote(tag)}&body={quote(body)}"


def _band(score):
    for floor, bg, fg in _BANDS:
        if score >= floor:
            return bg, fg
    return _BANDS[-1][1], _BANDS[-1][2]


def _button(href, label, primary=False):
    """Bulletproof-ish button: a padded, rounded table cell wrapping the link."""
    if primary:
        cell, color, border = "#1a73e8", "#ffffff", "#1a73e8"
    else:
        cell, color, border = "#ffffff", "#3c4043", "#dadce0"
    return f"""<table role="presentation" cellpadding="0" cellspacing="0" border="0"
        style="display:inline-block;margin:0 8px 0 0;"><tr><td
        style="background:{cell};border:1px solid {border};border-radius:8px;
        padding:8px 14px;" class="btn"><a href="{html.escape(href)}"
        style="color:{color};font:600 13px/1 {FONT};text-decoration:none;
        display:block;white-space:nowrap;" class="btn-a">{label}</a></td></tr></table>"""


def _card(i, j, s, is_new):
    e = html.escape
    bg, fg = _band(s["match_score"])
    meta = " &middot; ".join(filter(None, [
        e(j["company"]), e(j["location"]),
        e(j["salary"]) if j.get("salary")
        else '<span style="color:#8a5300;">pay undisclosed</span>',
    ]))
    new_badge = ("""<span style="background:#146c2e;color:#fff;border-radius:4px;
        font:600 10px/1 %s;padding:4px 6px;margin-left:8px;vertical-align:middle;
        letter-spacing:.04em;">NEW</span>""" % FONT) if is_new else ""

    tags = []
    if j.get("role_family"):
        tags.append(e(j["role_family"]))
    if s.get("seniority_fit") == "stretch":
        tags.append("stretch")
    if j.get("years_required"):
        tags.append(f"{j['years_required']}+ yrs asked")
    if j.get("no_salary"):
        tags.append(f"posted &lt;{NO_SALARY_MAX_AGE_DAYS}d &middot; verify pay")
    tag_html = ""
    if tags:
        chips = "".join(
            f"""<span style="background:#f1f3f4;color:#5f6368;border-radius:4px;
            padding:3px 7px;font:500 11px/1.4 {FONT};margin:0 6px 0 0;
            display:inline-block;" class="chip">{t}</span>""" for t in tags)
        tag_html = f'<div style="margin:0 0 10px;">{chips}</div>'

    missing = ", ".join(s.get("missing_skills") or [])
    missing_html = ""
    if missing:
        missing_html = (f'<div style="font:400 12px/1.5 {FONT};color:#80868b;'
                        f'margin:8px 0 0;" class="dim">Gaps: {e(missing)}</div>')

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
  style="background:#ffffff;border:1px solid #e4e6e9;border-radius:12px;margin:0 0 12px;"
  class="card">
  <tr><td style="padding:18px 20px 16px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
      <tr>
        <td style="vertical-align:top;padding:0 12px 0 0;">
          <div style="font:600 16px/1.35 {FONT};color:#17181a;" class="title">{i}. {e(j['title'])}{new_badge}</div>
          <div style="font:400 13px/1.6 {FONT};color:#5f6368;margin:3px 0 10px;" class="dim">{meta}</div>
        </td>
        <td width="52" style="vertical-align:top;text-align:right;">
          <div style="background:{bg};color:{fg};border-radius:999px;padding:6px 0;
            font:700 15px/1 {FONT};text-align:center;width:52px;" class="pill">{s['match_score']}</div>
          <div style="font:400 10px/1.4 {FONT};color:#9aa0a6;text-align:center;
            width:52px;margin:4px 0 0;" class="dim">{round(s['confidence'] * 100)}% conf</div>
        </td>
      </tr>
    </table>
    {tag_html}
    <div style="font:400 14px/1.6 {FONT};color:#3c4043;margin:0 0 2px;" class="body">{e(s['why'])}</div>
    {missing_html}
    <div style="margin:16px 0 0;">
      {_button(j['url'], 'View posting', primary=True)}
      {_button(_mailto(j, SUBJECT_TAG, 'applied'), 'Mark applied')}
      {_button(_mailto(j, REJECTED_SUBJECT_TAG, 'rejected'), 'Not a fit')}
    </div>
    <div style="font:400 11px/1.5 {FONT};color:#b0b4b8;margin:10px 0 0;" class="dim">via {e(j['source'])}</div>
  </td></tr>
</table>"""


_DARK_CSS = """
@media (prefers-color-scheme: dark) {
  .wrap { background:#17181a !important; }
  .card { background:#212327 !important; border-color:#3a3d42 !important; }
  .title, .body { color:#e8eaed !important; }
  .dim { color:#9aa0a6 !important; }
  .chip { background:#2d3034 !important; color:#bdc1c6 !important; }
  .btn { background:#212327 !important; border-color:#4a4e54 !important; }
  .btn-a { color:#e8eaed !important; }
  .btn[style*="1a73e8"] { background:#8ab4f8 !important; border-color:#8ab4f8 !important; }
  .btn[style*="1a73e8"] .btn-a { color:#17181a !important; }
  .rule { border-color:#3a3d42 !important; }
}
@media only screen and (max-width:600px) {
  .card td { padding:14px 14px 12px !important; }
}
"""


def build_html(top, stats):
    e = html.escape
    new_ids = set(stats.get("new_ids") or [])
    cards = "".join(_card(i, j, s, j["id"] in new_ids) for i, (j, s) in enumerate(top, 1))
    if not cards:
        cards = (f'<table role="presentation" width="100%" class="card" style="background:#fff;'
                 f'border:1px solid #e4e6e9;border-radius:12px;"><tr><td style="padding:24px;'
                 f'font:400 14px/1.6 {FONT};color:#5f6368;" class="dim">Nothing cleared the '
                 f'filters today. The next run will pick up newly posted roles.</td></tr></table>')

    suggestions = sorted({t for _, s in top for t in s.get("resume_suggestions", [])})
    sugg_html = ""
    if suggestions:
        items = "".join(
            f'<li style="margin:0 0 6px;">{e(t)}</li>' for t in suggestions)
        sugg_html = f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
  style="background:#ffffff;border:1px solid #e4e6e9;border-radius:12px;margin:0 0 12px;" class="card">
  <tr><td style="padding:18px 20px;">
    <div style="font:600 13px/1.4 {FONT};color:#17181a;margin:0 0 10px;
      text-transform:uppercase;letter-spacing:.06em;" class="title">Resume suggestions</div>
    <ul style="margin:0;padding:0 0 0 18px;font:400 13px/1.6 {FONT};color:#3c4043;" class="body">{items}</ul>
  </td></tr></table>"""

    profile_note = (f'<li style="margin:0 0 4px;">Skill profile: {e(stats["profile_note"])}</li>'
                    if stats.get("profile_note") else "")
    boards = stats.get("boards")
    boards_line = (f'<li style="margin:0 0 4px;">{boards} employer ATS boards in the registry</li>'
                   if boards else "")

    return f"""<div style="background:#f1f3f4;margin:0;padding:24px 12px;" class="wrap">
<style>{_DARK_CSS}</style>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
  style="max-width:640px;margin:0 auto;">
  <tr><td>
    <div style="font:700 20px/1.3 {FONT};color:#17181a;margin:0 0 4px;" class="title">
      Career Agent</div>
    <div style="font:400 13px/1.6 {FONT};color:#5f6368;margin:0 0 4px;" class="dim">
      Top {len(top)} matches &middot; {e(stats['date'])}</div>
    <div style="font:400 12px/1.6 {FONT};color:#80868b;margin:0 0 18px;" class="dim">
      Fully remote &middot; full-time &middot; stated salary from ${MIN_SALARY_USD:,}
      &middot; applied and rejected roles suppressed<br>
      Up to {NO_SALARY_MAX_IN_DIGEST} slots may go to employer-direct roles posted within
      {NO_SALARY_MAX_AGE_DAYS} days that don't disclose pay, and only at
      {NO_SALARY_MIN_SCORE}+ match with a clear step up in scope</div>
    {cards}
    {sugg_html}
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
      style="background:#ffffff;border:1px solid #e4e6e9;border-radius:12px;margin:0 0 12px;" class="card">
      <tr><td style="padding:18px 20px;">
        <div style="font:600 13px/1.4 {FONT};color:#17181a;margin:0 0 10px;
          text-transform:uppercase;letter-spacing:.06em;" class="title">Run summary</div>
        <ul style="margin:0;padding:0 0 0 18px;font:400 13px/1.6 {FONT};color:#5f6368;" class="dim">
          <li style="margin:0 0 4px;">{stats['evaluated']} roles evaluated across {stats['sources']} sources</li>
          {boards_line}
          <li style="margin:0 0 4px;">{stats['new']} newly discovered &middot; {stats['expired']} expired out</li>
          <li style="margin:0 0 4px;">{stats.get('applied_total', 0)} applied &middot;
            {stats.get('rejected_total', 0)} marked not a fit &middot;
            {stats.get('discovered_total', 0)} discovered all-time</li>
          {profile_note}
        </ul>
      </td></tr>
    </table>
    <div style="border-top:1px solid #dadce0;margin:20px 0 12px;" class="rule"></div>
    <div style="font:400 11px/1.6 {FONT};color:#9aa0a6;" class="dim">
      &ldquo;Mark applied&rdquo; and &ldquo;Not a fit&rdquo; each compose an email to yourself &mdash;
      send either as-is and the next run records it. A role marked not a fit stops appearing
      even if it is re-posted at a new link, and teaches the ranker to deprioritize similar
      postings; other roles at that company are unaffected.
      This digest is informational only &mdash; nothing was applied to on your behalf.
      Sources: employer job boards on Greenhouse, Lever, Ashby and SmartRecruiters, plus
      Remotive, Adzuna, USAJobs, Jooble and JSearch.
    </div>
  </td></tr>
</table>
</div>"""


def build_text(top, stats):
    """Real plain-text alternative — some clients show it, and it is what
    lands in a text-only forward or a screen reader's linear read."""
    lines = [f"CAREER AGENT — top {len(top)} matches · {stats['date']}",
             f"Fully remote · full-time · stated salary from ${MIN_SALARY_USD:,}", ""]
    for i, (j, s) in enumerate(top, 1):
        bits = [j["company"], j["location"]]
        bits.append(j["salary"] if j.get("salary")
                    else "pay undisclosed - verify before applying")
        lines += [
            f"{i}. {j['title']}",
            f"   {' · '.join(bits)}",
            f"   Match {s['match_score']}/100 · {round(s['confidence'] * 100)}% confidence · via {j['source']}",
            f"   {s['why']}",
        ]
        if s.get("missing_skills"):
            lines.append(f"   Gaps: {', '.join(s['missing_skills'])}")
        lines += [f"   Apply: {j['url']}",
                  f"   Mark applied: reply-to-self with subject {SUBJECT_TAG}, body: applied {j['id']}",
                  f"   Not a fit:    reply-to-self with subject {REJECTED_SUBJECT_TAG}, body: rejected {j['id']}",
                  ""]
    if not top:
        lines += ["Nothing cleared the filters today.", ""]
    lines += ["—",
              f"{stats['evaluated']} roles evaluated across {stats['sources']} sources · "
              f"{stats['new']} new · {stats['expired']} expired",
              "Informational only — nothing was applied to on your behalf."]
    return "\n".join(lines)


def send_digest(top, stats, dry_run=False):
    body = build_html(top, stats)
    text = build_text(top, stats)
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
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(body, "html", "utf-8"))
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

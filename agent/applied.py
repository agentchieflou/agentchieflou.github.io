"""Applied-jobs tracking, driven entirely from the email digest.

Each job card in the digest carries a "mark applied" mailto link that
composes a self-addressed email with a CA-APPLIED subject and the job id in
the body. At the start of every run the agent logs into the owner's own
mailbox over IMAP (same Gmail app password as SMTP), reads unseen
CA-APPLIED messages, and records the ids here. Applied jobs are excluded
from future digests and from the public graph.

Privacy: agent-data is a public branch, so applied.json stores only opaque
job ids and dates — never titles, companies, or URLs. Nothing on the
website indicates application activity.
"""
import datetime as dt
import email
import imaplib
import re

from config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD, STATE_DIR
from util import load_json, log, save_json

APPLIED_PATH = STATE_DIR / "applied.json"
SUBJECT_TAG = "CA-APPLIED"
_ID_RE = re.compile(r"\bapplied\s+([0-9a-f]{16,64})\b", re.I)


def load_applied():
    return load_json(APPLIED_PATH, {})


def _message_text(msg):
    parts = []
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
    return "\n".join(parts)


def sync_from_inbox():
    """Merges ids from unseen CA-APPLIED emails; returns the applied dict."""
    applied = load_applied()
    if not GMAIL_APP_PASSWORD:
        log.info("GMAIL_APP_PASSWORD not set - skipping applied-email sync")
        return applied
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
        m.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        m.select("INBOX")
        # Fetching RFC822 (no PEEK) marks the message seen, so each is
        # processed exactly once even if the id regex finds nothing.
        _, data = m.search(None, f'(UNSEEN SUBJECT "{SUBJECT_TAG}")')
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        added = 0
        for num in (data[0].split() if data and data[0] else []):
            _, msg_data = m.fetch(num, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            for jid in _ID_RE.findall(_message_text(msg)):
                if jid not in applied:
                    applied[jid] = {"applied_at": now}
                    added += 1
        m.logout()
        if added:
            save_json(APPLIED_PATH, applied)
        log.info("applied sync: %d new, %d total marked applied", added, len(applied))
    except Exception as e:
        log.warning("applied-email sync failed (IMAP): %s", e)
    return applied

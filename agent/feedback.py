"""Shared machinery for the two email-driven feedback loops.

applied.py and rejected.py are the same mechanism pointed at different
subject tags: read unseen self-addressed tagged mail, pull job ids out of the
body, record them so those jobs stop being surfaced. This module holds the
part they share so the behaviour can only ever be fixed once.

Every record stores two identifiers:

  job id    — sha1 of the posting URL. Exact, but a role re-posted at a fresh
              URL (or surfaced by a second source) is a different id, so on
              its own a suppressed job comes straight back a week later.
  role key  — opaque hash of normalized company + title (util.role_key).
              Survives re-posting, and is scoped to one role at one employer,
              so suppressing "Data Analyst at Acme" never touches any other
              Acme posting. Employers are never blacklisted.

Privacy: agent-data is a public branch, so these files hold only opaque ids,
hashes and dates — never titles, companies, or URLs.
"""
import datetime as dt
import email
import imaplib
import re

from config import GMAIL_ADDRESS, GMAIL_APP_PASSWORD, STATE_DIR
from util import load_json, log, role_key, save_json

SEEN_PATH = STATE_DIR / "seen_jobs.json"
# The digest writes "(Title @ Company)" under the id line so a record is still
# resolvable after the posting has expired out of seen_jobs.json.
_LABEL_RE = re.compile(r"^\s*\((.+?)\s+@\s+(.+?)\)\s*$", re.M)


def role_keys(records):
    """The set of opaque company+title hashes to suppress."""
    return {v.get("role_key") for v in records.values()
            if isinstance(v, dict) and v.get("role_key")}


def _message_text(msg):
    parts = []
    for part in msg.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
    return "\n".join(parts)


def _resolve_role_key(jid, body, seen):
    entry = seen.get(jid) or {}
    if entry.get("company") and entry.get("title"):
        return role_key(entry["company"], entry["title"])
    m = _LABEL_RE.search(body or "")
    if m:
        return role_key(m.group(2), m.group(1))
    return ""


def backfill_role_keys(path, records, label):
    """Fills in role keys for records written before they were tracked, for as
    long as the posting is still in seen_jobs.json."""
    seen = load_json(SEEN_PATH, {})
    added = 0
    for jid, entry in records.items():
        if not isinstance(entry, dict) or entry.get("role_key"):
            continue
        rk = _resolve_role_key(jid, "", seen)
        if rk:
            entry["role_key"] = rk
            added += 1
    if added:
        save_json(path, records)
        log.info("%s: backfilled %d role keys from seen-job state", label, added)
    return records


def sync(tag, verb, path, stamp_field):
    """Reads unseen `tag` mail and merges the ids it names into `path`.

    `verb` is the word preceding the id in the body ("applied"/"rejected").
    """
    records = load_json(path, {})
    if not GMAIL_APP_PASSWORD:
        log.info("GMAIL_APP_PASSWORD not set - skipping %s-email sync", verb)
        return backfill_role_keys(path, records, verb)
    id_re = re.compile(rf"\b{verb}\s+([0-9a-f]{{16,64}})\b", re.I)
    seen = load_json(SEEN_PATH, {})
    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
        m.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        m.select("INBOX")
        # Fetching RFC822 (no PEEK) marks the message seen, so each is
        # processed exactly once even if the id regex finds nothing.
        _, data = m.search(None, f'(UNSEEN SUBJECT "{tag}")')
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        added = 0
        for num in (data[0].split() if data and data[0] else []):
            _, msg_data = m.fetch(num, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            body = _message_text(email.message_from_bytes(msg_data[0][1]))
            for jid in id_re.findall(body):
                if jid not in records:
                    records[jid] = {stamp_field: now,
                                    "role_key": _resolve_role_key(jid, body, seen)}
                    added += 1
        m.logout()
        if added:
            save_json(path, records)
        log.info("%s sync: %d new, %d total (%d role keys)",
                 verb, added, len(records), len(role_keys(records)))
    except Exception as e:
        log.warning("%s-email sync failed (IMAP): %s", verb, e)
    return backfill_role_keys(path, records, verb)

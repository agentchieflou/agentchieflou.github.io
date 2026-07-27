"""Applied-jobs tracking, driven entirely from the email digest.

Each job card in the digest carries a "mark applied" link that composes a
self-addressed email with a CA-APPLIED subject and the job id in the body. At
the start of every run the agent logs into the owner's own mailbox over IMAP
(same Gmail app password as SMTP), reads unseen CA-APPLIED messages, and
records the ids. Applied roles are excluded from future digests and from the
public graph.

See feedback.py for the shared mechanism, including why each record carries
an opaque company+title role key alongside the job id — without it, a role
you already applied to reappears the moment it is re-posted at a new URL —
and for an honest account of what "opaque ids on a public branch" does and
does not protect.
"""
import feedback
from config import STATE_DIR

APPLIED_PATH = STATE_DIR / "applied.json"
SUBJECT_TAG = "CA-APPLIED"


def load_applied():
    from util import load_json
    return load_json(APPLIED_PATH, {})


def applied_role_keys(applied=None):
    return feedback.role_keys(load_applied() if applied is None else applied)


def sync_from_inbox():
    """Merges ids from unseen CA-APPLIED emails; returns the applied dict."""
    return feedback.sync(SUBJECT_TAG, "applied", APPLIED_PATH, "applied_at")

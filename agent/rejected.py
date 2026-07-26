"""Bad-match tracking, driven entirely from the email digest.

Each job card in the digest carries a "not a good match" link that composes a
self-addressed email with a CA-REJECTED subject and the job id in the body.
At the start of every run the agent reads unseen CA-REJECTED messages over
IMAP and records them.

Rejected roles are excluded from discovery onward — before ranking,
enrichment and scoring, not just before the digest — and their embeddings
feed the negative-feedback centroid in rank.py so structurally similar
postings rank lower going forward.

See feedback.py for the shared mechanism, including why each rejection is
recorded under both a job id and an opaque company+title role key (short
version: the id is a URL hash, so it alone lets a re-post come back), and why
that key is never company-only.
"""
import feedback
from config import STATE_DIR

REJECTED_PATH = STATE_DIR / "rejected.json"
SUBJECT_TAG = "CA-REJECTED"


def load_rejected():
    from util import load_json
    return load_json(REJECTED_PATH, {})


def rejected_role_keys(rejected=None):
    return feedback.role_keys(load_rejected() if rejected is None else rejected)


def sync_from_inbox():
    """Merges ids from unseen CA-REJECTED emails; returns the rejected dict."""
    return feedback.sync(SUBJECT_TAG, "rejected", REJECTED_PATH, "rejected_at")

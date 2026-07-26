"""Application pre-flight: read the form before spending time on it.

Greenhouse publishes a posting's entire application question set read-only at
`/v1/boards/{token}/jobs/{id}?questions=true`, and that is worth more than it
sounds. Postings routinely carry *required* screeners the job text never
mentions. A live example from a role that had already cleared every remote
filter in this pipeline:

    "Are you based in, or very close to, Las Vegas, Nevada? Thanks!"

That is half an hour of form-filling wasted, discovered at the end. Pulling
the questions first turns it into a line in the digest.

For each digest job hosted on Greenhouse this stage:

  * flags likely blockers — a location screener on a supposedly-remote role,
    a security clearance requirement, an experience bar past the owner's band;
  * pre-fills every answer that is deterministic from the profile, including
    the standing answer to "how did you hear about us";
  * lists the free-text questions so they get drafted deliberately instead of
    improvised into a form field.

It deliberately does NOT draft the free-text answers. "Why do you want to
work here" answered by a language model is the single most recognisable
tell of a mass application, and this pipeline's whole advantage is that
every application is considered. Listing them early is the useful part.

Nothing is ever submitted. Greenhouse's POST endpoint requires the
employer's own API key — it answers `401 HTTP Basic: Access denied` without
one — and submitting applications is outside this agent's remit regardless.
"""
import re

import requests

import role_filter
from config import (APPLICANT, MAX_YEARS_REQUIRED_OVER_CANDIDATE,
                    REFERRAL_ANSWER, STATE_DIR, USER_AGENT)
from util import load_json, log, save_json

CACHE_PATH = STATE_DIR / "preflight_cache.json"
MAX_PER_RUN = 20

_GREENHOUSE_URL = re.compile(
    r"https?://(?:boards|job-boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)", re.I)
# Employers often link their own careers page with the id in the query string.
_GREENHOUSE_GHJID = re.compile(r"[?&]gh_jid=(\d+)")

# Questions every form asks, mapped to a settled answer.
_DETERMINISTIC = {
    "first_name": APPLICANT["first_name"],
    "last_name": APPLICANT["last_name"],
    "email": APPLICANT["email"],
    "phone": APPLICANT["phone"],
    "location": APPLICANT["location"],
}
_BY_LABEL = [
    (re.compile(r"how did you (?:first\s+)?(?:hear|find out|learn|come)\s+"
                r"(?:about|across)|referr?al source|where did you (?:hear|find)",
                re.I), REFERRAL_ANSWER),
    (re.compile(r"^preferred (?:first )?name", re.I), APPLICANT["first_name"]),
    (re.compile(r"linked\s*-?in", re.I), APPLICANT["linkedin"]),
    (re.compile(r"git\s*hub", re.I), APPLICANT["github"]),
    (re.compile(r"portfolio|personal (?:web)?site|website", re.I), APPLICANT["website"]),
    (re.compile(r"current (?:city|location)|where are you (?:based|located)", re.I),
     APPLICANT["location"]),
]

# Screeners that can disqualify regardless of how well the role otherwise fits.
_BLOCKERS = [
    ("on-site location requirement", re.compile(
        r"\b(?:based in|located in|live in|reside in|commute|commuting|relocat|"
        r"willing to work (?:in|from)|able to work (?:in|from)|"
        r"within .{0,20} (?:miles|minutes) of)\b", re.I)),
    ("security clearance", re.compile(
        r"\b(?:security clearance|active clearance|ts/sci|public trust|polygraph)\b", re.I)),
    ("advanced degree required", re.compile(
        r"\b(?:master'?s|mba|ph\.?d|doctorate)\b.{0,40}\b(?:required|do you have)\b", re.I)),
]
_YEARS = re.compile(r"(\d{1,2})\+?\s*(?:or more\s*)?years", re.I)

# "Which U.S. State do you reside in?" is a records question with a fifty-item
# dropdown, not a requirement. The screeners that actually disqualify name one
# place and want a yes or no.
_LOCATION_INFORMATIONAL = re.compile(
    r"\bwhich\b.{0,24}\b(?:state|province|country|region|city)\b|"
    r"\bwhat\b.{0,16}\b(?:state|province|country|city)\b", re.I)


def _option_count(question):
    return max((len(f.get("values") or []) for f in (question.get("fields") or [])),
               default=0)


def _greenhouse_ref(job):
    """(board_token, job_id) for a Greenhouse-hosted posting, else None."""
    url = job.get("url") or ""
    m = _GREENHOUSE_URL.match(url)
    if m:
        return m.group(1), m.group(2)
    # Plenty of employers front their Greenhouse board with their own careers
    # page (stripe.com/jobs/search?gh_jid=...). The id is still in the query
    # string; the board token comes from the registry that found the posting.
    board, ghjid = job.get("board"), _GREENHOUSE_GHJID.search(url)
    if board and ghjid:
        return board, ghjid.group(1)
    return None


def _fetch(token, job_id):
    r = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}",
        params={"questions": "true"},
        headers={"User-Agent": USER_AGENT}, timeout=25)
    r.raise_for_status()
    return r.json()


def _answer_for(question):
    """A settled answer for this question, or None if it needs a human."""
    fields = question.get("fields") or []
    name = (fields[0].get("name") if fields else "") or ""
    for key, value in _DETERMINISTIC.items():
        if name == key or name.endswith("_" + key):
            return value
    label = question.get("label") or ""
    for pat, value in _BY_LABEL:
        if pat.search(label):
            return value
    return None


def _is_upload(question):
    types = {f.get("type") for f in (question.get("fields") or [])}
    return bool(types & {"input_file"})


def _blockers_for(question, job):
    """Blocker labels this question raises for this specific job."""
    label = question.get("label") or ""
    if not question.get("required"):
        return []
    found = []
    for name, pat in _BLOCKERS:
        if not pat.search(label):
            continue
        if name == "on-site location requirement":
            # Only matters because the posting claimed to be remote.
            if not job.get("remote", True):
                continue
            # Skip the "where do you live" records questions.
            if _LOCATION_INFORMATIONAL.search(label) or _option_count(question) > 5:
                continue
        found.append(name)
    for raw in _YEARS.findall(label):
        years = int(raw)
        if years > role_filter.candidate_years() + MAX_YEARS_REQUIRED_OVER_CANDIDATE:
            found.append(f"asks for {years}+ years")
            break
    return found


def analyze(job):
    """Returns a pre-flight dict for one job, or None when unavailable."""
    ref = _greenhouse_ref(job)
    if not ref:
        return None
    try:
        detail = _fetch(*ref)
    except Exception as e:
        log.info("preflight fetch failed for %s: %s", job.get("title", "")[:40], e)
        return None

    questions = detail.get("questions") or []
    if not questions:
        return None

    answered, to_draft, uploads, blockers = [], [], [], []
    for q in questions:
        label = (q.get("label") or "").strip()
        if not label:
            continue
        blockers.extend(f"{label} — {b}" for b in _blockers_for(q, job))
        if _is_upload(q):
            uploads.append(label)
            continue
        answer = _answer_for(q)
        if answer:
            answered.append({"question": label, "answer": answer})
        elif q.get("required"):
            to_draft.append(label)

    return {
        "total": len(questions),
        "answered": answered,
        "to_draft": to_draft[:8],
        "uploads": uploads,
        "blockers": blockers[:4],
    }


def annotate(top):
    """Attaches `preflight` to each (job, score) pair in the digest selection.

    Cached by job id: a posting's form does not change between runs, and the
    cap keeps a bad day from turning into a burst of requests at one employer.
    """
    cache = load_json(CACHE_PATH, {})
    fetched = 0
    for job, _score in top:
        jid = job["id"]
        if jid in cache:
            if cache[jid]:
                job["preflight"] = cache[jid]
            continue
        if not _greenhouse_ref(job) or fetched >= MAX_PER_RUN:
            continue
        fetched += 1
        result = analyze(job)
        cache[jid] = result or {}
        if result:
            job["preflight"] = result

    while len(cache) > 1000:
        del cache[next(iter(cache))]
    save_json(CACHE_PATH, cache)

    flagged = sum(1 for j, _ in top if j.get("preflight", {}).get("blockers"))
    covered = sum(1 for j, _ in top if j.get("preflight"))
    if fetched or covered:
        log.info("preflight: %d forms read (%d fetched this run), %d with likely blockers",
                 covered, fetched, flagged)
    return top

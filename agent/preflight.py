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
import standing_answers
from config import (MAX_YEARS_REQUIRED_OVER_CANDIDATE, STATE_DIR, USER_AGENT)
from util import load_json, log, save_json

CACHE_PATH = STATE_DIR / "preflight_cache.json"
MAX_PER_RUN = 20
# Bump whenever the blocker rules change: a cached verdict was produced by the
# rules of its day, and leaving stale ones in place means a fixed
# false positive keeps being reported.
ANALYSIS_VERSION = 4

_GREENHOUSE_URL = re.compile(
    r"https?://(?:boards|job-boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)", re.I)
# Employers often link their own careers page with the id in the query string.
_GREENHOUSE_GHJID = re.compile(r"[?&]gh_jid=(\d+)")

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
# A years figure is only an experience bar when the question says so.
# "Are you at least 18 years of age?" is on nearly every form and was being
# read as an 18-year experience requirement.
_EXPERIENCE_CONTEXT = re.compile(r"\bexperience\b|\bworking\b|\bprofessional\b", re.I)
_AGE_CONTEXT = re.compile(r"\bage\b|\bolder\b|\blegally\b", re.I)


# What separates a disqualifier from a records question is the stem, not the
# field shape. Counting options looked right until a real one turned up:
# "Are you currently located within 50 miles of one of the locations listed
# below?" offers six cities, so an option-count rule suppressed it — while
# "Which U.S. State do you reside in?" merely records an address.
#
#   Are you / Do you / Will you  -> asking whether you qualify
#   Which / What / Where         -> asking where you live
_REQUIREMENT_STEM = re.compile(
    r"^\s*(?:are|do|does|did|will|would|can|could|is|have|has|must)\b", re.I)


def _asks_whether(question):
    return bool(_REQUIREMENT_STEM.match(question.get("label") or ""))


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
            # Only matters because the posting claimed to be remote, and only
            # when it is actually a screener rather than a records question.
            if not job.get("remote", True) or not _asks_whether(question):
                continue
        found.append(name)
    if _EXPERIENCE_CONTEXT.search(label) and not _AGE_CONTEXT.search(label):
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

    standing, specific, uploads, blockers = 0, [], [], []
    for q in questions:
        label = (q.get("label") or "").strip()
        if not label:
            continue
        blockers.extend(f"{label} — {b}" for b in _blockers_for(q, job))
        if _is_upload(q):
            uploads.append(label)
            continue
        answer, _rule = standing_answers.resolve(q)
        if answer:
            standing += 1
        elif q.get("required"):
            # Everything left is genuinely about this job, this company, or a
            # judgment only the owner can make — the questions worth their
            # attention, which is the entire point of separating them out.
            specific.append({"q": label, "options": _short_options(q)})

    return {
        "total": len(questions),
        "standing": standing,
        "specific": specific[:8],
        "uploads": uploads,
        "blockers": blockers[:4],
    }


def _short_options(question):
    """Option labels for a select, so the digest shows what can be picked."""
    opts = []
    for field in question.get("fields") or []:
        for value in field.get("values") or []:
            if value.get("label"):
                opts.append(value["label"][:60])
    return opts[:6]


def annotate(top):
    """Attaches `preflight` to each (job, score) pair in the digest selection.

    Cached by job id: a posting's form does not change between runs, and the
    cap keeps a bad day from turning into a burst of requests at one employer.
    """
    cache = load_json(CACHE_PATH, {})
    fetched = 0
    for job, _score in top:
        jid = job["id"]
        cached = cache.get(jid)
        if isinstance(cached, dict) and cached.get("v") == ANALYSIS_VERSION:
            if cached.get("total"):
                job["preflight"] = cached
            continue
        if not _greenhouse_ref(job) or fetched >= MAX_PER_RUN:
            continue
        fetched += 1
        result = analyze(job) or {}
        result["v"] = ANALYSIS_VERSION
        cache[jid] = result
        if result.get("total"):
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

"""Relevance gates applied to every posting, from every source.

The ATS board APIs (agent/ats.py) return *every* open role at a company —
recruiters, nurses, warehouse staff, iOS engineers — so a title-level filter
is what makes those sources usable at all. The same gates run against the
aggregator sources, where they replace the old "whatever the query returned"
behaviour that produced most of the off-target matches.

Three independent gates, each a hard drop:

  role family  — the posting has to belong to one of the owner's target
                 disciplines (business analytics, technically-focused process
                 optimization, technically-focused strategy, AI/ML).
  seniority    — the posting has to sit in the owner's reachable band. A
                 Principal/Staff/Director/VP role is not a step up, it is a
                 different career stage, and scoring it 95/100 is noise.
  employment   — full-time only; contract, part-time and internships are out.

Everything here is title-and-description pattern matching: no API cost, no
LLM call, and it runs before ranking so the expensive stages only ever see
plausible roles.
"""
import datetime as dt
import re

from config import (CAREER_START_YEAR, MAX_YEARS_REQUIRED_OVER_CANDIDATE,
                    ROLE_FAMILIES, TITLE_EXCLUDE_PATTERNS,
                    SENIORITY_EXCLUDE_PATTERNS, EMPLOYMENT_EXCLUDE_PATTERNS)
from util import log


def candidate_years():
    """Years of professional experience, derived so it never goes stale."""
    return max(0, dt.date.today().year - CAREER_START_YEAR)


_EXCLUDE = re.compile("|".join(TITLE_EXCLUDE_PATTERNS), re.I)
_SENIORITY_EXCLUDE = re.compile("|".join(SENIORITY_EXCLUDE_PATTERNS), re.I)
_EMPLOYMENT_EXCLUDE = re.compile("|".join(EMPLOYMENT_EXCLUDE_PATTERNS), re.I)
_FAMILIES = [(name, re.compile("|".join(pats), re.I)) for name, pats in ROLE_FAMILIES.items()]

# "5+ years", "5-7 years", "minimum of 8 years", "at least 10 years experience"
_YEARS_REQUIRED = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(?:\d{1,2})?\s*\+?\s*years?"
    r"(?:\s+of)?(?:\s+(?:relevant|related|progressive|professional|industry|hands-on))?"
    r"\s+(?:work\s+)?experience", re.I)


def role_family(title):
    """Returns the matching target-discipline name, or None."""
    title = title or ""
    if _EXCLUDE.search(title):
        return None
    for name, pat in _FAMILIES:
        if pat.search(title):
            return name
    return None


def seniority_ok(title):
    """False for bands the owner should not be spending digest slots on."""
    return not _SENIORITY_EXCLUDE.search(title or "")


def years_required(text):
    """The posting's stated experience bar in years, or None.

    Two deliberate choices. Within a range ("3-5 years") the lower bound is
    the requirement — that is the number you have to clear to be considered.
    Across several separate statements the largest wins, because postings
    routinely put a soft number in the summary and the real one deep in the
    requirements list.
    """
    vals = [int(m) for m in _YEARS_REQUIRED.findall((text or "")[:8000])]
    vals = [v for v in vals if 0 < v <= 30]
    return max(vals) if vals else None


def employment_ok(title, description="", employment_type=None):
    """Full-time only.

    `employment_type` is the ATS's own structured value where one exists
    (Ashby, SmartRecruiters) and is trusted over text matching when present.
    """
    if employment_type:
        return bool(re.match(r"full[\s_-]?time", str(employment_type).strip(), re.I))
    if _EMPLOYMENT_EXCLUDE.search(title or ""):
        return False
    # Only the opening of the description — benefits sections mention
    # "part-time employees" in ways that say nothing about this role.
    return not _EMPLOYMENT_EXCLUDE.search((description or "")[:600])


def evaluate(job):
    """Returns (keep: bool, reason: str). Annotates the job when kept."""
    title = job.get("title") or ""

    if not employment_ok(title, job.get("description"), job.get("employment_type")):
        return False, "not full-time"

    family = role_family(title)
    if not family:
        return False, "off-discipline"

    if not seniority_ok(title):
        return False, "out-of-band seniority"

    req = years_required(job.get("description"))
    if req is not None and req > candidate_years() + MAX_YEARS_REQUIRED_OVER_CANDIDATE:
        return False, f"requires {req}y experience"

    job["role_family"] = family
    if req is not None:
        job["years_required"] = req
    return True, ""


def filter_jobs(jobs, label=""):
    """Applies every gate; logs a per-reason breakdown."""
    kept, reasons = [], {}
    for j in jobs:
        ok, why = evaluate(j)
        if ok:
            kept.append(j)
        else:
            reasons[why] = reasons.get(why, 0) + 1
    if reasons:
        breakdown = ", ".join(f"{v} {k}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]))
        log.info("relevance gate%s: %d of %d kept (dropped: %s)",
                 f" [{label}]" if label else "", len(kept), len(jobs), breakdown)
    return kept

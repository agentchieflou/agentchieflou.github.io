"""Company accessibility gate: "we want to work for companies that make
themselves accessible."

Purely mechanical, no LLM. For each employer that shows up in the fetched
job pool:
  1. Resolve a company name to a real domain via Clearbit's free, keyless
     autocomplete endpoint (accepted only when the result's name is a close
     match — this is a fuzzy lookup, not a guess-the-domain heuristic).
  2. Fetch that domain's homepage and extract visible text as the company's
     self-description.
  3. If that comes back too thin (JS-shell site, fetch failure), fall back
     to an "About {Company}" style blurb already present in one of the
     company's own job postings, if one exists.
  4. No resolvable domain AND no usable fallback => the company is marked
     inaccessible and every job at that company is dropped, this run and
     future ones (until a periodic recheck, in case they later stand up a
     real site).

Companies not yet checked (beyond the per-run cap) are left alone rather
than excluded — the cache builds up gradually across runs, same pattern as
enrich.py's finalist-description backfill, so a cold cache doesn't crater
the pool on day one.
"""
import re

import requests

from config import (COMPANY_DESC_MIN_USEFUL, COMPANY_ENRICH_MAX_PER_RUN,
                    COMPANY_RECHECK_DAYS, STATE_DIR, USER_AGENT)
from util import html_to_text, load_json, log, norm_key, save_json

CACHE_PATH = STATE_DIR / "company_cache.json"
CLEARBIT_SUGGEST = "https://autocomplete.clearbit.com/v1/companies/suggest"


def _resolve_domain(company):
    try:
        r = requests.get(CLEARBIT_SUGGEST, params={"query": company},
                         headers={"User-Agent": USER_AGENT}, timeout=15)
        r.raise_for_status()
        results = r.json()
    except Exception as e:
        log.info("company domain lookup failed for %s: %s", company, e)
        return None
    target = norm_key(company)
    for hit in results:
        name = norm_key(hit.get("name", ""))
        domain = hit.get("domain")
        if not domain or not name:
            continue
        if name == target or name.startswith(target) or target.startswith(name):
            return domain
    return None


def _fetch_homepage_text(domain):
    for scheme in ("https://", "http://"):
        try:
            r = requests.get(f"{scheme}{domain}", headers={"User-Agent": USER_AGENT},
                             timeout=15, allow_redirects=True)
            r.raise_for_status()
            text = html_to_text(r.text)
            if len(text) >= COMPANY_DESC_MIN_USEFUL:
                return text[:1500]
        except Exception as e:
            log.info("company homepage fetch failed for %s: %s", domain, e)
    return None


_ABOUT_SENTENCE = re.compile(
    r"(?:About\s+[A-Z][\w&.,' -]{1,40}|[A-Z][\w&.,' -]{1,40}\s+(?:is|are|builds|provides|"
    r"offers|helps|makes)\b)[^.]{20,400}\.", re.M)


def _about_snippet_from_postings(company, job_descriptions):
    """Best-effort fallback: an "About {Company}" style sentence already
    sitting in one of the company's own postings, so a company with a bad
    marketing site but a real self-description in its listing isn't
    punished for a website that just renders badly."""
    for desc in job_descriptions:
        for m in _ABOUT_SENTENCE.finditer(desc or ""):
            snippet = m.group(0).strip()
            if len(snippet) >= COMPANY_DESC_MIN_USEFUL // 2:
                return snippet[:1000]
    return None


def _needs_check(entry, now_iso):
    if entry is None:
        return True
    if entry.get("accessible"):
        return False  # resolved once, stays resolved — no need to recheck
    checked = entry.get("checked_at")
    if not checked:
        return True
    import datetime as dt
    age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(checked)
    return age.days >= COMPANY_RECHECK_DAYS


def gate_jobs(jobs):
    """Filters `jobs` in place-ish: returns only jobs whose employer is
    known-accessible or not-yet-checked. Definitively inaccessible
    companies are dropped. Mutates each surviving job with
    `company_description` when known."""
    import datetime as dt
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    cache = load_json(CACHE_PATH, {})

    by_company = {}
    for j in jobs:
        by_company.setdefault(j["company"], []).append(j)

    checked_this_run = 0
    for company, company_jobs in by_company.items():
        key = norm_key(company)
        if not key:
            continue
        entry = cache.get(key)
        if not _needs_check(entry, now_iso):
            continue
        if checked_this_run >= COMPANY_ENRICH_MAX_PER_RUN:
            continue
        checked_this_run += 1

        domain = _resolve_domain(company)
        desc = _fetch_homepage_text(domain) if domain else None
        if not desc:
            desc = _about_snippet_from_postings(company, [j["description"] for j in company_jobs])
        cache[key] = {
            "company": company,
            "domain": domain,
            "accessible": bool(desc),
            "description": desc,
            "checked_at": now_iso,
        }

    save_json(CACHE_PATH, cache)

    kept, dropped = [], 0
    for j in jobs:
        entry = cache.get(norm_key(j["company"]))
        if entry is not None and entry.get("accessible") is False:
            dropped += 1
            continue
        if entry and entry.get("description"):
            j["company_description"] = entry["description"]
        kept.append(j)

    if checked_this_run:
        log.info("company gate: checked %d new employers this run", checked_this_run)
    if dropped:
        log.info("company gate: dropped %d jobs from %d inaccessible companies",
                 dropped, sum(1 for e in cache.values() if e.get("accessible") is False))
    return kept

"""Fetches real posting text for finalist jobs that arrived without any.

Doomers snapshot records carry structured fields (salary, remote, seniority)
but no description, so the LLM would otherwise score them on title alone.
For only the top prefiltered candidates each run, this stage pulls the
description straight from the employer's ATS — via the ATS's public JSON API
where one exists (cleanest and lightest), falling back to the posting page.

The same lookup doubles as a liveness check: when the ATS itself says the
posting no longer exists (404 / null), the job is marked dead and dropped —
aggregator snapshots lag reality, and a digest slot spent on a closed role
is wasted.

Footprint: results are cached by job id, the stage is capped per run, and
every request targets the employer's own high-capacity ATS, never the
aggregator that surfaced the link.
"""
import html as html_mod
import json
import re

import requests

from config import STATE_DIR, USER_AGENT
from util import html_to_text, load_json, log, save_json

CACHE_PATH = STATE_DIR / "enrich_cache.json"
MAX_PER_RUN = 15
MIN_USEFUL = 200      # extracted text shorter than this = extraction failed
MAX_TEXT = 6000
DEAD = "__DEAD__"     # cache marker: the ATS confirmed the posting is gone


class PostingGone(Exception):
    pass


def _get_json(url, **kw):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20, **kw)
    if r.status_code == 404:
        raise PostingGone(url)
    r.raise_for_status()
    return r.json()


_ASHBY_QUERY = """query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    id title descriptionHtml
  }
}"""


def _ashby(m):
    r = requests.post(
        "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting",
        json={"operationName": "ApiJobPosting", "query": _ASHBY_QUERY,
              "variables": {"organizationHostedJobsPageName": m[1], "jobPostingId": m[2]}},
        headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    posting = (r.json().get("data") or {}).get("jobPosting")
    if not posting:
        raise PostingGone(m[0])
    return html_to_text(posting.get("descriptionHtml") or "")


def _greenhouse(m):
    d = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}")
    return html_to_text(html_mod.unescape(d.get("content", "")))


def _lever(m):
    d = _get_json(f"https://api.lever.co/v0/postings/{m[1]}/{m[2]}")
    lists = " ".join(f"{l.get('text', '')}: {html_to_text(l.get('content', ''))}"
                     for l in d.get("lists", []))
    return " ".join(filter(None, [d.get("descriptionPlain") or html_to_text(d.get("description", "")), lists]))


def _smartrecruiters(m):
    d = _get_json(f"https://api.smartrecruiters.com/v1/companies/{m[1]}/postings/{m[2]}")
    sections = (d.get("jobAd") or {}).get("sections") or {}
    return " ".join(html_to_text(s.get("text", "")) for s in sections.values()
                    if isinstance(s, dict))


def _workday(m):
    tenant, wd, site, rest = m[1], m[2], m[3], m[4]
    d = _get_json(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/{rest}")
    return html_to_text((d.get("jobPostingInfo") or {}).get("jobDescription", ""))


# Recognized ATS URL shapes -> JSON API handler. Workday's regex tolerates an
# optional locale segment (".../en-us/{site}/job/...").
_HANDLERS = [
    (re.compile(r"https?://(?:boards|job-boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)"), _greenhouse),
    (re.compile(r"https?://jobs(?:\.eu)?\.lever\.co/([^/]+)/([0-9a-f-]{36})"), _lever),
    (re.compile(r"https?://jobs\.smartrecruiters\.com/([^/]+)/(\d+)"), _smartrecruiters),
    (re.compile(r"https?://jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})"), _ashby),
    (re.compile(r"https?://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-zA-Z]{2}-[a-zA-Z]{2,4}/)?([^/]+)/job/(.+)"), _workday),
]


def _fetch_description(url):
    for pat, fn in _HANDLERS:
        m = pat.match(url)
        if m:
            return fn(m)
    # Generic fallback: the posting page itself (useless for JS-shell sites,
    # which the MIN_USEFUL check catches).
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    r.raise_for_status()
    return html_to_text(r.text)


def enrich_candidates(candidates):
    """Fills in thin descriptions for the given finalists, in place."""
    cache = load_json(CACHE_PATH, {})
    fetched = dead = 0
    for j in candidates:
        cached = cache.get(j["id"])
        if cached == DEAD:
            j["dead"] = True
            continue
        if cached:
            j["description"] = (j["description"] + " " + cached)[:MAX_TEXT]
            continue
        if cached == "" or len(j["description"]) >= 300 or fetched >= MAX_PER_RUN:
            continue
        fetched += 1
        try:
            text = re.sub(r"\s+", " ", _fetch_description(j["url"]) or "").strip()
        except PostingGone:
            cache[j["id"]] = DEAD
            j["dead"] = True
            dead += 1
            continue
        except Exception as e:
            log.info("enrich failed for %s (%s): %s", j["title"][:40], j["source"], e)
            text = ""
        if len(text) < MIN_USEFUL:
            cache[j["id"]] = ""  # remember the failure; don't refetch daily
            continue
        cache[j["id"]] = text[:MAX_TEXT]
        j["description"] = (j["description"] + " " + cache[j["id"]])[:MAX_TEXT]
    if dead:
        log.info("enrich: %d finalists dropped — posting gone at the ATS", dead)
    # Cap the cache by age (insertion order) — finalists rotate, so entries
    # must outlive a single run's candidate list.
    while len(cache) > 2000:
        del cache[next(iter(cache))]
    if fetched:
        log.info("enriched %d finalist descriptions from ATS sources", fetched)
    save_json(CACHE_PATH, cache)
    return candidates

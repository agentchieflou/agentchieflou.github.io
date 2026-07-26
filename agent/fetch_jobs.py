"""Fetches remote job postings from API-backed sources and normalizes them.

Source policy: every source here is an official, documented API. Nothing is
scraped. That is a deliberate narrowing — the previous mix leaned on
unstructured sources (Hacker News comment threads, community snapshots) whose
postings carried no reliable employment type, seniority or compensation, and
those were the ones producing off-target matches.

  agent/ats.py       employer ATS boards (Greenhouse, Lever, Ashby,
                     SmartRecruiters) — the highest-fidelity input, straight
                     from the employer, and the bulk of the pool
  Remotive           keyless remote-only aggregator API
  Adzuna             free key; queried with a server-side salary floor
  USAJobs            free key; federal remote roles
  Jooble             free key; broad aggregation
  JSearch (RapidAPI) free tier; wraps many providers including Google Jobs

LinkedIn, Indeed and ZipRecruiter are absent on purpose: no usable public API
and hostile to automated access, so there is no polite way to include them.

The agent is strictly read-only against these services: it fetches public
listings and nothing else. No applications, no accounts, no outreach.
"""
import datetime as dt
import re

import requests

import ats
import role_filter
from config import (ADZUNA_APP_ID, ADZUNA_APP_KEY, JOB_EXPIRY_DAYS,
                    JOOBLE_API_KEY, MIN_SALARY_USD, RAPIDAPI_KEY,
                    SEARCH_QUERIES, STATE_DIR, USAJOBS_API_KEY,
                    USAJOBS_USER_AGENT, USER_AGENT)
from util import (find_salary_snippet, html_to_text, load_json, log,
                  looks_genuinely_remote, norm_key, role_key, salary_max_usd,
                  save_json, sha1, us_friendly)

SEEN_PATH = STATE_DIR / "seen_jobs.json"
TOTALS_PATH = STATE_DIR / "totals.json"


def _get(url, **kw):
    kw.setdefault("timeout", 30)
    headers = kw.pop("headers", {})
    headers.setdefault("User-Agent", USER_AGENT)
    return requests.get(url, headers=headers, **kw)


def _job(source, title, company, location, url, description, salary=None,
         posted_at=None, remote=True, employment_type=None):
    title = (title or "").strip()[:200]
    company = (company or "").strip()[:120]
    description = re.sub(r"\s+", " ", description or "").strip()
    return {
        "id": sha1(url),
        "source": source,
        "title": title,
        "company": company,
        "location": (location or "Remote").strip()[:120],
        "remote": bool(remote),
        "url": url,
        "description": description[:6000],
        "salary": salary,
        "posted_at": posted_at,
        "employment_type": employment_type,
        "content_hash": sha1(title + "|" + company + "|" + description[:2000]),
    }


def _from_ats(raw):
    return _job(raw["source"], raw["title"], raw["company"], raw["location"],
                raw["url"], raw["description"], raw.get("salary"),
                raw.get("posted_at"), True, raw.get("employment_type"))


# ---------------- keyless sources ----------------

def fetch_ats_boards(target_titles):
    return [_from_ats(r) for r in ats.fetch_boards()]


def fetch_remotive(target_titles):
    r = _get("https://remotive.com/api/remote-jobs?limit=200")
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = j.get("candidate_required_location", "")
        if not us_friendly(loc):
            continue
        out.append(_job("remotive", j.get("title"), j.get("company_name"), loc or "Remote",
                        j.get("url"), html_to_text(j.get("description", "")),
                        j.get("salary") or None, j.get("publication_date"),
                        employment_type=j.get("job_type")))
    return out


# ---------------- free-key sources ----------------

def fetch_adzuna(target_titles):
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        log.info("Adzuna secrets not set - skipping source")
        return []
    out = []
    for title in SEARCH_QUERIES[:6]:
        # salary_min and full_time are applied server-side, so the response is
        # already inside the owner's constraints instead of being filtered
        # down to nothing on this end.
        r = _get("https://api.adzuna.com/v1/api/jobs/us/search/1",
                 params={"app_id": ADZUNA_APP_ID, "app_key": ADZUNA_APP_KEY,
                         "results_per_page": 50, "what": f"{title} remote",
                         "salary_min": MIN_SALARY_USD, "full_time": 1,
                         "max_days_old": 30, "content-type": "application/json"})
        r.raise_for_status()
        for j in r.json().get("results", []):
            blob = " ".join([j.get("title", ""), (j.get("location") or {}).get("display_name", ""),
                             j.get("description", "")])
            if not looks_genuinely_remote(blob):
                continue
            salary = None
            if j.get("salary_min"):
                salary = f"${int(j['salary_min']):,}-${int(j.get('salary_max') or j['salary_min']):,}"
            out.append(_job("adzuna", j.get("title"), (j.get("company") or {}).get("display_name"),
                            "Remote (US)", j.get("redirect_url"), j.get("description", ""),
                            salary, j.get("created"), employment_type="Full-time"))
    return out


def fetch_usajobs(target_titles):
    if not USAJOBS_API_KEY:
        log.info("USAJobs secret not set - skipping source")
        return []
    r = _get("https://data.usajobs.gov/api/search",
             params={"Keyword": " OR ".join(SEARCH_QUERIES[:4]), "RemoteIndicator": "True",
                     "ResultsPerPage": 50, "PositionScheduleTypeCode": "1"},
             headers={"Authorization-Key": USAJOBS_API_KEY, "User-Agent": USAJOBS_USER_AGENT,
                      "Host": "data.usajobs.gov"})
    r.raise_for_status()
    out = []
    for item in r.json().get("SearchResult", {}).get("SearchResultItems", []):
        d = item.get("MatchedObjectDescriptor", {})
        remun = (d.get("PositionRemuneration") or [{}])[0]
        salary = None
        if remun.get("MinimumRange"):
            salary = f"${float(remun['MinimumRange']):,.0f}-${float(remun.get('MaximumRange', 0)):,.0f}"
        desc = " ".join(filter(None, [
            (d.get("UserArea", {}).get("Details", {}) or {}).get("JobSummary", ""),
            d.get("QualificationSummary", "")]))
        out.append(_job("usajobs", d.get("PositionTitle"), d.get("OrganizationName"),
                        "Remote (US Federal)", d.get("PositionURI"), desc, salary,
                        d.get("PublicationStartDate"), employment_type="Full-time"))
    return out


def fetch_jooble(target_titles):
    if not JOOBLE_API_KEY:
        log.info("Jooble secret not set - skipping source")
        return []
    out = []
    for title in SEARCH_QUERIES[:4]:
        r = requests.post(f"https://jooble.org/api/{JOOBLE_API_KEY}",
                          json={"keywords": title, "location": "Remote",
                                "salary": str(MIN_SALARY_USD), "page": "1"},
                          headers={"User-Agent": USER_AGENT,
                                   "Content-Type": "application/json"}, timeout=30)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            blob = " ".join([j.get("title", ""), j.get("location", ""), j.get("snippet", "")])
            if not looks_genuinely_remote(blob):
                continue
            out.append(_job("jooble", j.get("title"), j.get("company"),
                            j.get("location") or "Remote", j.get("link"),
                            html_to_text(j.get("snippet", "")), j.get("salary") or None,
                            j.get("updated"), employment_type=j.get("type")))
    return out


def fetch_jsearch(target_titles):
    """JSearch on RapidAPI — wraps Google Jobs and many boards behind one API.

    Free tier is small, so this asks for one page per title and leans on the
    API's own remote/full-time filters rather than over-fetching.
    """
    if not RAPIDAPI_KEY:
        log.info("RapidAPI secret not set - skipping JSearch source")
        return []
    out = []
    for title in SEARCH_QUERIES[:3]:
        r = _get("https://jsearch.p.rapidapi.com/search",
                 params={"query": f"{title} in United States", "page": "1",
                         "num_pages": "1", "work_from_home": "true",
                         "employment_types": "FULLTIME", "date_posted": "month"},
                 headers={"X-RapidAPI-Key": RAPIDAPI_KEY,
                          "X-RapidAPI-Host": "jsearch.p.rapidapi.com"})
        r.raise_for_status()
        for j in r.json().get("data", []):
            if not j.get("job_is_remote"):
                continue
            salary = None
            if j.get("job_max_salary"):
                period = j.get("job_salary_period") or "YEAR"
                mult = {"HOUR": 2080, "MONTH": 12, "WEEK": 52}.get(period, 1)
                lo = int((j.get("job_min_salary") or 0) * mult)
                hi = int(j["job_max_salary"] * mult)
                salary = f"${lo:,}-${hi:,}" if lo else f"up to ${hi:,}"
            out.append(_job("jsearch", j.get("job_title"), j.get("employer_name"),
                            "Remote (US)", j.get("job_apply_link"),
                            j.get("job_description", ""), salary,
                            j.get("job_posted_at_datetime_utc"),
                            employment_type=j.get("job_employment_type")))
    return out


# ---------------- orchestration ----------------

SOURCES = [
    ("ats", fetch_ats_boards),
    ("remotive", fetch_remotive),
    ("adzuna", fetch_adzuna),
    ("usajobs", fetch_usajobs),
    ("jooble", fetch_jooble),
    ("jsearch", fetch_jsearch),
]


def fetch_all(target_titles, exclude_role_keys=()):
    """Returns (jobs, new_ids, expired_count, totals). Updates seen state.

    `exclude_role_keys` are opaque company+title hashes the owner has already
    rejected; dropping them here means a re-posted rejected role never reaches
    embedding, enrichment or scoring, not just the digest.

    `totals` tracks the cumulative count of qualifying jobs ever discovered,
    surviving expiry/replacement — the site's "N discovered to date" figure.
    """
    jobs, per_source = [], {}
    for name, fn in SOURCES:
        try:
            found = fn(target_titles)
            per_source[name] = len(found)
            jobs.extend(found)
        except Exception as e:
            log.warning("source %s failed: %s", name, e)
            per_source[name] = 0
    log.info("fetched per source: %s", per_source)

    # Aggregator results that link straight at an employer's ATS teach the
    # registry a new board to poll directly from the next run onward.
    ats.record_discovered(jobs)

    # Defense in depth: every job builder above is expected to set `remote`
    # honestly, but a bad source integration must never be able to sneak a
    # non-remote posting through just by omitting the check.
    before_remote = len(jobs)
    jobs = [j for j in jobs if j.get("remote")]
    if len(jobs) != before_remote:
        log.info("remote-only rule: dropped %d non-remote postings", before_remote - len(jobs))

    # Discipline, seniority and full-time gates. Runs before everything
    # expensive: these are the filters that decide whether the pool is
    # actually reachable roles or just remote roles.
    jobs = role_filter.filter_jobs(jobs)

    # A role the owner already rejected stays rejected even when it comes
    # back at a new URL (re-posted, or surfaced by a different source).
    if exclude_role_keys:
        before = len(jobs)
        jobs = [j for j in jobs if role_key(j["company"], j["title"]) not in exclude_role_keys]
        if len(jobs) != before:
            log.info("rejected-role rule: dropped %d re-posted rejected roles", before - len(jobs))

    # Stated-salary requirement: a posting must state compensation somewhere
    # (structured field, or extractable from its text) and it must reach the
    # floor. Employers that won't say what they pay don't make the list.
    before = len(jobs)
    kept, no_salary = [], 0
    for j in jobs:
        if not j.get("salary"):
            snippet = find_salary_snippet(j.get("description"))
            if snippet:
                j["salary"] = snippet + " (from posting text)"
        mx = salary_max_usd(j.get("salary"))
        if mx is None:
            no_salary += 1
        elif mx >= MIN_SALARY_USD:
            kept.append(j)
    jobs = kept
    log.info("stated-salary rule: %d of %d kept (%d no stated salary, %d below $%s)",
             len(jobs), before, no_salary, before - no_salary - len(jobs),
             f"{MIN_SALARY_USD:,}")

    # Dedupe across sources by URL id, then by normalized company+title.
    # Sorted so the employer's own ATS posting wins over an aggregator's copy
    # of the same role — better URL, better data.
    _SOURCE_RANK = {"greenhouse": 0, "lever": 0, "ashby": 0, "smartrecruiters": 0}
    jobs.sort(key=lambda j: _SOURCE_RANK.get(j["source"], 1))
    by_id, by_key = {}, {}
    for j in jobs:
        key = (norm_key(j["company"]), norm_key(j["title"]))
        if j["id"] in by_id or (all(key) and key in by_key):
            continue
        by_id[j["id"]] = j
        if all(key):
            by_key[key] = j["id"]
    jobs = list(by_id.values())

    now = dt.datetime.now(dt.timezone.utc)
    seen = load_json(SEEN_PATH, {})
    new_ids = [j["id"] for j in jobs if j["id"] not in seen]
    for j in jobs:
        entry = seen.setdefault(j["id"], {"first_seen": now.isoformat()})
        entry.update(last_seen=now.isoformat(), content_hash=j["content_hash"],
                     title=j["title"], company=j["company"], source=j["source"], url=j["url"])

    cutoff = now - dt.timedelta(days=JOB_EXPIRY_DAYS)
    expired = [jid for jid, e in seen.items()
               if dt.datetime.fromisoformat(e["last_seen"]) < cutoff]
    for jid in expired:
        del seen[jid]
    save_json(SEEN_PATH, seen)

    totals = load_json(TOTALS_PATH, {})
    if "all_time" not in totals:
        totals["all_time"] = len(seen)  # seed from what's already known
    else:
        totals["all_time"] += len(new_ids)
    totals["live"] = len(jobs)
    totals["updated_at"] = now.isoformat()
    save_json(TOTALS_PATH, totals)
    return jobs, new_ids, len(expired), totals

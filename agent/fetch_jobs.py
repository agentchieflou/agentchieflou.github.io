"""Fetches remote job postings from free sources and normalizes them.

Sources (all free): Remotive, RemoteOK, Arbeitnow, HN "Who is hiring"
(keyless) plus Adzuna and USAJobs (free API keys via repo secrets; each is
skipped with a log line when its secret is absent).

The agent is strictly read-only against these services: it fetches public
listings and nothing else. No applications, no accounts, no outreach.
"""
import datetime as dt
import re

import requests

from config import (ADZUNA_APP_ID, ADZUNA_APP_KEY, DOOMERS_BASE,
                    DOOMERS_GROUPS, DOOMERS_MAX_PER_GROUP, JOB_EXPIRY_DAYS,
                    MIN_SALARY_USD, STATE_DIR, USAJOBS_API_KEY,
                    USAJOBS_USER_AGENT, USER_AGENT)
from util import (find_salary_snippet, html_to_text, load_json, log,
                  looks_genuinely_remote, norm_key, salary_max_usd, save_json,
                  sha1)

SEEN_PATH = STATE_DIR / "seen_jobs.json"
TOTALS_PATH = STATE_DIR / "totals.json"

US_REMOTE_HINTS = re.compile(
    r"\b(usa|u\.s\.|united states|americas|north america|worldwide|anywhere|global)\b", re.I)


def _get(url, **kw):
    kw.setdefault("timeout", 30)
    headers = kw.pop("headers", {})
    headers.setdefault("User-Agent", USER_AGENT)
    return requests.get(url, headers=headers, **kw)


def _job(source, title, company, location, url, description, salary=None, posted_at=None, remote=True):
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
        "description": description[:4000],
        "salary": salary,
        "posted_at": posted_at,
        "content_hash": sha1(title + "|" + company + "|" + description[:2000]),
    }


def _us_friendly(location_text):
    return not location_text or bool(US_REMOTE_HINTS.search(location_text))


# ---------------- keyless sources ----------------

def fetch_remotive():
    r = _get("https://remotive.com/api/remote-jobs?limit=200")
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        loc = j.get("candidate_required_location", "")
        if not _us_friendly(loc):
            continue
        out.append(_job("remotive", j.get("title"), j.get("company_name"), loc or "Remote",
                        j.get("url"), html_to_text(j.get("description", "")),
                        j.get("salary") or None, j.get("publication_date")))
    return out


def fetch_remoteok():
    r = _get("https://remoteok.com/api")
    r.raise_for_status()
    out = []
    for j in r.json():
        if not isinstance(j, dict) or not j.get("position"):
            continue  # first element is their legal notice
        loc = j.get("location", "")
        if loc and not _us_friendly(loc):
            continue
        salary = None
        if j.get("salary_min") and j.get("salary_max"):
            salary = f"${j['salary_min']:,}-${j['salary_max']:,}"
        out.append(_job("remoteok", j.get("position"), j.get("company"), loc or "Remote",
                        j.get("url"), html_to_text(j.get("description", "")),
                        salary, j.get("date")))
    return out


def fetch_arbeitnow():
    out = []
    url = "https://www.arbeitnow.com/api/job-board-api"
    for _ in range(2):  # first 2 pages
        r = _get(url)
        r.raise_for_status()
        data = r.json()
        for j in data.get("data", []):
            if not j.get("remote"):
                continue
            out.append(_job("arbeitnow", j.get("title"), j.get("company_name"),
                            j.get("location") or "Remote", j.get("url"),
                            html_to_text(j.get("description", "")), None,
                            str(j.get("created_at", ""))))
        url = (data.get("links") or {}).get("next")
        if not url:
            break
    return out


def fetch_hn_whoishiring():
    r = _get("https://hn.algolia.com/api/v1/search_by_date",
             params={"query": '"Ask HN: Who is hiring?"', "tags": "story,author_whoishiring",
                     "hitsPerPage": 1})
    r.raise_for_status()
    hits = r.json().get("hits", [])
    if not hits:
        return []
    story_id = hits[0]["objectID"]
    r = _get("https://hn.algolia.com/api/v1/search",
             params={"tags": f"comment,story_{story_id}", "hitsPerPage": 150})
    r.raise_for_status()
    out = []
    for c in r.json().get("hits", []):
        text = html_to_text(c.get("comment_text") or "")
        if not text or not looks_genuinely_remote(text):
            continue
        first_line = text.splitlines()[0]
        segs = [s.strip() for s in first_line.split("|")]
        company = segs[0][:80] if segs else "See posting"
        title = segs[1] if len(segs) > 1 else "See posting"
        out.append(_job("hn", title, company, "Remote",
                        f"https://news.ycombinator.com/item?id={c['objectID']}",
                        text, None, c.get("created_at")))
    return out


# ---------------- free-key sources ----------------

def fetch_adzuna(target_titles):
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        log.info("Adzuna secrets not set - skipping source")
        return []
    out = []
    for title in target_titles[:2]:  # keep request count minimal
        r = _get("https://api.adzuna.com/v1/api/jobs/us/search/1",
                 params={"app_id": ADZUNA_APP_ID, "app_key": ADZUNA_APP_KEY,
                         "results_per_page": 50, "what": f"{title} remote",
                         "content-type": "application/json"})
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
                            salary, j.get("created")))
    return out


def fetch_usajobs(target_titles):
    if not USAJOBS_API_KEY:
        log.info("USAJobs secret not set - skipping source")
        return []
    r = _get("https://data.usajobs.gov/api/search",
             params={"Keyword": " OR ".join(target_titles[:3]), "RemoteIndicator": "True",
                     "ResultsPerPage": 50},
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
                        d.get("PublicationStartDate")))
    return out


# ---------------- doomersareretardedcommunists.com ----------------

DOOMERS_CACHE_PATH = STATE_DIR / "doomers_cache.json"


def _doomers_normalize(raw):
    """Normalize one enriched snapshot record to our job shape, or None.

    Only enriched records carry a structured remote flag and salary, so
    non-enriched records are skipped entirely — no guessing from URLs.
    """
    if not raw.get("enriched"):
        return None
    loc = raw.get("location") or {}
    if not loc.get("remote") or loc.get("country") != "US":
        return None
    sal = raw.get("salary") or {}
    salary_str, yearly_max = None, None
    if sal.get("max_cents"):
        mult = {"hourly": 2080, "monthly": 12}.get(sal.get("period"), 1)
        lo = int((sal.get("min_cents") or 0) / 100 * mult)
        hi = int(sal["max_cents"] / 100 * mult)
        salary_str = f"${lo:,}-${hi:,}" if lo else f"up to ${hi:,}"
        yearly_max = hi
    # Stated salary is a hard requirement — enriched records without one
    # are dropped here rather than wasting the pipeline's attention.
    if yearly_max is None or yearly_max < MIN_SALARY_USD:
        return None
    desc_bits = [
        f"{raw.get('title', '')} at {raw.get('company', '')}.",
        "Fully remote (US).",
        f"Categories: {', '.join(raw.get('job_categories') or [])}." if raw.get("job_categories") else "",
        f"Seniority: {raw['seniority_level']}." if raw.get("seniority_level") else "",
        f"Salary: {salary_str}." if salary_str else "",
    ]
    job = _job("doomers", raw.get("title"), raw.get("company"), "Remote (US)",
               raw.get("link"), " ".join(b for b in desc_bits if b),
               salary_str, raw.get("created_at"))
    return job if job["url"] and job["title"] else None


def fetch_doomers():
    """Snapshot-aware fetch: manifest is tiny and checked every run; the big
    per-group files are fetched only when the snapshot date changes, and the
    filtered result is cached so an unchanged day costs one HTTP request."""
    cache = load_json(DOOMERS_CACHE_PATH, {})
    try:
        r = _get(f"{DOOMERS_BASE}/data/current/manifest.json")
        r.raise_for_status()
        manifest = r.json()
    except Exception as e:
        log.warning("doomers manifest unavailable (%s) - using cached parse", e)
        return cache.get("jobs", [])
    snap = manifest.get("snapshot_date")
    if snap and cache.get("snapshot_date") == snap:
        log.info("doomers snapshot %s unchanged - using cached parse", snap)
        return cache.get("jobs", [])

    group_urls = {g["slug"]: g["url"] for g in manifest.get("groups", [])}
    jobs, failed = [], False
    for slug in DOOMERS_GROUPS:
        url = group_urls.get(slug)
        if not url:
            log.warning("doomers group %s missing from manifest", slug)
            continue
        try:
            r = _get(DOOMERS_BASE + url, timeout=120)
            r.raise_for_status()
            raw_jobs = r.json().get("jobs", [])
        except Exception as e:
            log.warning("doomers group %s fetch failed: %s", slug, e)
            failed = True
            continue
        found = [j for j in map(_doomers_normalize, raw_jobs) if j]
        found.sort(key=lambda j: j.get("posted_at") or "", reverse=True)
        jobs.extend(found[:DOOMERS_MAX_PER_GROUP])
        log.info("doomers %s: %d remote US jobs kept", slug, len(found[:DOOMERS_MAX_PER_GROUP]))

    if not jobs:
        return cache.get("jobs", [])
    # A partial failure keeps the old snapshot_date so the next run retries
    # the full download rather than considering this snapshot done.
    save_json(DOOMERS_CACHE_PATH, {
        "snapshot_date": cache.get("snapshot_date") if failed else snap,
        "jobs": jobs,
    })
    return jobs


# ---------------- orchestration ----------------

SOURCES = [
    ("remotive", lambda titles: fetch_remotive()),
    ("remoteok", lambda titles: fetch_remoteok()),
    ("arbeitnow", lambda titles: fetch_arbeitnow()),
    ("hn", lambda titles: fetch_hn_whoishiring()),
    ("adzuna", fetch_adzuna),
    ("usajobs", fetch_usajobs),
    ("doomers", lambda titles: fetch_doomers()),
]


def fetch_all(target_titles):
    """Returns (jobs, new_ids, expired_count, totals). Updates seen state.

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

    # Defense in depth: every job builder above is expected to set `remote`
    # honestly, but a bad source integration must never be able to sneak a
    # non-remote posting through just by omitting the check.
    before_remote = len(jobs)
    jobs = [j for j in jobs if j.get("remote")]
    if len(jobs) != before_remote:
        log.info("remote-only rule: dropped %d non-remote postings", before_remote - len(jobs))

    # Stated-salary requirement: a posting must state compensation somewhere
    # (salary field, or extractable from its text) and it must reach the
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

    # Dedupe across sources by URL id, then by normalized company+title
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

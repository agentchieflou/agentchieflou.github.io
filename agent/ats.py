"""Employer ATS job boards as first-class sources.

Greenhouse, Lever, Ashby and SmartRecruiters each publish a company's open
roles as structured JSON at a documented, keyless, stable endpoint. Compared
with the aggregators these are strictly better input:

  * the data comes from the employer, not a re-seller, so it is current and
    a closed role disappears instead of lingering;
  * employment type, workplace type and (on Ashby and Lever) compensation are
    real typed fields rather than something to guess out of prose;
  * the posting URL is the actual application page.

They are per-company rather than global, which is the one cost. Two things
handle that: agent/boards.json ships a verified seed registry, and every run
harvests ATS links out of the aggregator results to grow it (state file
discovered_boards.json). Boards are polled least-recently-first on a rotation
capped by ATS_BOARDS_PER_RUN, so the registry can grow without the run time
growing with it.

Politeness: one list request per board per rotation, detail requests only for
postings that already passed the title and remote gates, and a hard per-board
job cap.
"""
import datetime as dt
import html as html_mod
import json
import re
from concurrent.futures import ThreadPoolExecutor

import requests

import role_filter
from config import (ATS_BOARDS_PER_RUN, ATS_FETCH_WORKERS,
                    ATS_MAX_JOBS_PER_BOARD, BOARDS_PATH,
                    RELOCATION_SALARY_USD, STATE_DIR, USER_AGENT)
from util import (find_salary_snippet, html_to_text, load_json, log,
                  looks_us, salary_max_usd, save_json)

DISCOVERED_PATH = STATE_DIR / "discovered_boards.json"
POLL_PATH = STATE_DIR / "board_poll.json"

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT

ATS_NAMES = ("greenhouse", "lever", "ashby", "smartrecruiters")


def _get(url, **kw):
    kw.setdefault("timeout", 30)
    r = _session.get(url, **kw)
    r.raise_for_status()
    return r.json()


def _pretty(slug):
    """Lever and Ashby omit the company display name; the slug is all we get."""
    return re.sub(r"[-_]+", " ", slug).strip().title()


def _title_ok(title):
    """Discipline + seniority gate, applied while still inside the fetcher.

    role_filter.filter_jobs() would catch these anyway, but a board dump is
    mostly roles the owner will never want: rejecting them here keeps the
    per-source counts honest and, on Greenhouse and SmartRecruiters, avoids
    spending a detail request per posting to learn nothing.
    """
    title = title or ""
    return bool(role_filter.role_family(title)) and role_filter.seniority_ok(title)


# ---------------- registry ----------------

def load_registry():
    """Seed registry merged with everything discovered so far."""
    seed = load_json(BOARDS_PATH, {})
    found = load_json(DISCOVERED_PATH, {})
    out = {}
    for ats in ATS_NAMES:
        merged = {s for s in seed.get(ats, []) if isinstance(s, str)}
        merged |= {s for s in found.get(ats, []) if isinstance(s, str)}
        out[ats] = sorted(merged)
    return out


_DISCOVERY = [
    ("greenhouse", re.compile(r"https?://(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"https?://jobs(?:\.eu)?\.lever\.co/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"https?://jobs\.ashbyhq\.com/([a-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"https?://jobs\.smartrecruiters\.com/([a-zA-Z0-9_-]+)", re.I)),
]


def record_discovered(jobs):
    """Harvests ATS board slugs out of aggregator job URLs.

    This is what makes the registry compound: an Adzuna or Remotive hit that
    links to boards.greenhouse.io/acme teaches the agent that Acme has a
    Greenhouse board, and from the next run on that board is polled directly.
    """
    found = load_json(DISCOVERED_PATH, {})
    seed = load_json(BOARDS_PATH, {})
    added = 0
    for j in jobs:
        url = j.get("url") or ""
        for ats, pat in _DISCOVERY:
            m = pat.match(url)
            if not m:
                continue
            slug = m.group(1)
            if slug in ("embed", "jobs", "api"):
                continue
            known = set(seed.get(ats, [])) | set(found.get(ats, []))
            if slug not in known:
                found.setdefault(ats, []).append(slug)
                added += 1
    if added:
        for ats in found:
            found[ats] = sorted(set(found[ats]))
        save_json(DISCOVERED_PATH, found)
        log.info("board discovery: %d new ATS board(s) learned from aggregator links", added)
    return added


def _select_boards(registry):
    """Least-recently-polled boards first, round-robined across the ATSs.

    The interleave matters: a flat least-recently-polled sort puts every
    never-polled board at the same key, so the cap would be filled by
    whichever ATS happens to sort first and the others would never run. That
    silently cost us Ashby, which is the only source with structured
    compensation.
    """
    polls = load_json(POLL_PATH, {})
    queues = []
    for ats in ATS_NAMES:
        q = sorted(registry.get(ats, []), key=lambda s: polls.get(f"{ats}:{s}", ""))
        if q:
            queues.append([(ats, slug) for slug in q])

    selected = []
    while queues and len(selected) < ATS_BOARDS_PER_RUN:
        for q in list(queues):
            if len(selected) >= ATS_BOARDS_PER_RUN:
                break
            selected.append(q.pop(0))
            if not q:
                queues.remove(q)
    return selected


def _record_polls(pairs):
    polls = load_json(POLL_PATH, {})
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for ats, slug in pairs:
        polls[f"{ats}:{slug}"] = now
    save_json(POLL_PATH, polls)


# ---------------- salary normalization ----------------

def _fmt_range(lo, hi):
    lo, hi = int(lo or 0), int(hi or 0)
    if not hi:
        return None
    return f"${lo:,}-${hi:,}" if lo else f"up to ${hi:,}"


_INTERVAL_MULT = [
    (re.compile(r"hour", re.I), 2080),
    (re.compile(r"week", re.I), 52),
    (re.compile(r"month", re.I), 12),
    (re.compile(r"year|annual", re.I), 1),
]


def _annualize(value, interval):
    if not value:
        return 0
    for pat, mult in _INTERVAL_MULT:
        if pat.search(str(interval or "")):
            return value * mult
    return value


# ---------------- Greenhouse ----------------

def _relocation_grade(salary_or_text):
    """True when stated pay is high enough to be worth moving for.

    Prose has to go through find_salary_snippet, which validates the figure
    looks like compensation. salary_max_usd() alone takes the largest number
    it can find, so a posting mentioning "500,000 customers" or a $70B
    portfolio reads as a relocation-grade salary — that mistake let roughly
    180 on-site roles through before it was caught.
    """
    if not salary_or_text:
        return False
    text = str(salary_or_text)
    figure = (salary_max_usd(text) if len(text) <= 60
              else salary_max_usd(find_salary_snippet(text)))
    return (figure or 0) >= RELOCATION_SALARY_USD


def _greenhouse(slug):
    """One request per board, with the full posting text.

    content=true returns every posting body in a single call. That is both
    fewer requests than fetching each posting separately and the only way to
    read pay: Greenhouse has no structured compensation field, so the figure
    has to come out of the posting text — and without it there is no way to
    tell whether an on-site role clears the relocation bar.
    """
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    out = []
    for j in (data.get("jobs") or [])[:ATS_MAX_JOBS_PER_BOARD]:
        title = j.get("title") or ""
        loc = ((j.get("location") or {}).get("name") or "").strip()
        if not _title_ok(title) or not looks_us(loc):
            continue
        remote = "remote" in loc.lower()
        desc = html_to_text(html_mod.unescape(j.get("content") or ""))
        # Parse pay here, from the untruncated text. _job() clips the
        # description and find_salary_snippet only scans its opening, while
        # Greenhouse postings put compensation at the very bottom — so a
        # figure recovered later would miss the long ones.
        salary = find_salary_snippet(desc)
        # On-site roles only earn a slot by naming relocation-grade pay.
        if not remote and (salary_max_usd(salary) or 0) < RELOCATION_SALARY_USD:
            continue
        out.append({
            "source": "greenhouse",
            # Kept so preflight.py can read the application form even when
            # absolute_url points at the employer's own careers page.
            "board": slug,
            "title": title,
            "company": j.get("company_name") or _pretty(slug),
            "location": loc or "Remote",
            "url": j.get("absolute_url"),
            "description": desc,
            "salary": salary,         # Greenhouse has no comp field; parsed from text
            "posted_at": j.get("first_published") or j.get("updated_at"),
            "remote": remote,
        })
    return out


# ---------------- Lever ----------------

def _lever(slug):
    """Everything (including salaryRange) arrives in the single list call."""
    data = _get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in (data or [])[:ATS_MAX_JOBS_PER_BOARD]:
        title = j.get("text") or ""
        cats = j.get("categories") or {}
        if not _title_ok(title):
            continue
        country = (j.get("country") or "").upper()
        loc = cats.get("location") or "Remote"
        if country and country not in ("US", "USA"):
            continue
        if not country and not looks_us(loc):
            continue
        sal = j.get("salaryRange") or {}
        salary = None
        if sal.get("max"):
            interval = sal.get("interval") or ""
            salary = _fmt_range(_annualize(sal.get("min"), interval),
                                _annualize(sal.get("max"), interval))
        remote = str(j.get("workplaceType") or "").lower() == "remote"
        if not remote and not _relocation_grade(salary or j.get("salaryDescriptionPlain")):
            continue
        desc = " ".join(filter(None, [
            j.get("descriptionPlain") or html_to_text(j.get("description") or ""),
            " ".join(f"{l.get('text', '')}: {html_to_text(l.get('content', ''))}"
                     for l in (j.get("lists") or [])),
            j.get("salaryDescriptionPlain") or "",
        ]))
        out.append({
            "source": "lever",
            "title": title,
            "company": _pretty(slug),
            "location": loc,
            "url": j.get("hostedUrl") or j.get("applyUrl"),
            "description": desc,
            "salary": salary,
            "posted_at": j.get("createdAt"),
            "remote": remote,
            "employment_type": cats.get("commitment"),
        })
    return out


# ---------------- Ashby ----------------

def _ashby_salary(comp):
    """Structured comp: the annualized Salary component of the first tier."""
    for tier in (comp or {}).get("compensationTiers") or []:
        for c in tier.get("components") or []:
            if str(c.get("compensationType") or "").lower() != "salary":
                continue
            if str(c.get("currencyCode") or "USD").upper() != "USD":
                continue
            interval = c.get("interval") or "1 YEAR"
            return _fmt_range(_annualize(c.get("minValue"), interval),
                              _annualize(c.get("maxValue"), interval))
    return None


def _ashby(slug):
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    out = []
    for j in (data.get("jobs") or [])[:ATS_MAX_JOBS_PER_BOARD]:
        if not j.get("isListed", True):
            continue
        if not _title_ok(j.get("title")):
            continue
        locs = [j.get("location") or ""] + [
            (s.get("location") or "") for s in (j.get("secondaryLocations") or [])]
        countries = [((s.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")
                     for s in (j.get("secondaryLocations") or [])]
        blob = " ".join(filter(None, locs + countries))
        if not looks_us(blob):
            continue
        # isRemote is true for hybrid roles too; workplaceType is the honest one.
        remote = str(j.get("workplaceType") or "").lower() == "remote"
        salary = _ashby_salary(j.get("compensation"))
        if not remote and not _relocation_grade(salary):
            continue
        out.append({
            "source": "ashby",
            "title": (j.get("title") or "").strip(),
            "company": _pretty(slug),
            "location": j.get("location") or "Remote",
            "url": j.get("jobUrl") or j.get("applyUrl"),
            "description": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or ""),
            "salary": salary,
            "posted_at": j.get("publishedAt"),
            "remote": remote,
            "employment_type": j.get("employmentType"),
        })
    return out


# ---------------- SmartRecruiters ----------------

def _sr_employment(value):
    if isinstance(value, dict):
        return value.get("label") or value.get("id")
    return value


def _smartrecruiters(slug):
    data = _get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100")
    out = []
    for j in (data.get("content") or [])[:ATS_MAX_JOBS_PER_BOARD]:
        title = j.get("name") or ""
        loc = j.get("location") or {}
        if not loc.get("remote"):
            continue
        if (loc.get("country") or "").lower() not in ("us", "usa", ""):
            continue
        if not _title_ok(title):
            continue
        company = (j.get("company") or {}).get("identifier") or slug
        try:
            detail = _get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{j['id']}")
        except Exception:
            continue
        sections = ((detail.get("jobAd") or {}).get("sections") or {})
        desc = " ".join(html_to_text(s.get("text", "")) for s in sections.values()
                        if isinstance(s, dict))
        out.append({
            "source": "smartrecruiters",
            "title": title,
            "company": (j.get("company") or {}).get("name") or _pretty(slug),
            "location": loc.get("fullLocation") or "Remote (US)",
            "url": f"https://jobs.smartrecruiters.com/{company}/{j['id']}",
            "description": desc,
            "salary": None,
            "posted_at": j.get("releasedDate"),
            "remote": True,
            "employment_type": _sr_employment(j.get("typeOfEmployment")),
        })
    return out


_FETCHERS = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "ashby": _ashby,
    "smartrecruiters": _smartrecruiters,
}


# ---------------- orchestration ----------------

def fetch_boards():
    """Polls this run's slice of the registry; returns raw job dicts.

    Board failures are logged and skipped — a company that took its board
    down must not take the run down with it.
    """
    registry = load_registry()
    pairs = _select_boards(registry)
    if not pairs:
        log.info("ATS registry empty - no boards to poll")
        return []
    total = sum(len(v) for v in registry.values())
    log.info("polling %d of %d registered ATS boards", len(pairs), total)

    def one(pair):
        ats, slug = pair
        try:
            return _FETCHERS[ats](slug)
        except Exception as e:
            log.info("ATS board %s/%s failed: %s", ats, slug, e)
            return []

    jobs, per_ats = [], {}
    with ThreadPoolExecutor(max_workers=ATS_FETCH_WORKERS) as ex:
        for (ats, _slug), found in zip(pairs, ex.map(one, pairs)):
            per_ats[ats] = per_ats.get(ats, 0) + len(found)
            jobs.extend(found)
    _record_polls(pairs)
    log.info("ATS boards returned: %s", per_ats or "nothing")
    return [j for j in jobs if j.get("url") and j.get("title")]

# Career Agent

A scheduled pipeline (`.github/workflows/career-agent.yml`) that:

1. Indexes `resume.html` + GitHub repos (READMEs, topics, commits) into a
   structured skill profile — via one Gemini call, **only when the source
   material changes**.
2. Fetches postings from **API-backed sources only** — nothing is scraped.
   The bulk comes from employer ATS boards (`ats.py`: Greenhouse, Lever,
   Ashby, SmartRecruiters), topped up by Remotive (keyless) and Adzuna,
   USAJobs, Jooble, JSearch/RapidAPI (free keys). See "Sources" below.
3. Applies three hard relevance gates (`role_filter.py`) before anything
   expensive runs: **discipline** (the owner's target families — business
   analytics, technically-focused process optimization and strategy, AI/ML),
   **seniority** (no Principal/Staff/Director/VP, no entry level, nothing
   asking for more than ~2 years beyond the owner's experience), and
   **full-time only**. **A stated salary is required** and must reach
   `MIN_SALARY_USD` ($130k, see `config.py`). Skills matching
   `SKILL_BLOCKLIST_PATTERNS` are stripped from the profile so they never
   influence matching or the graph.
4. Prefilters with local embeddings (fastembed ONNX — zero API cost),
   enriches finalists that lack posting text straight from the employer's
   ATS (`enrich.py`: Greenhouse/Lever/SmartRecruiters/Workday/Ashby public
   JSON endpoints; postings the ATS reports gone are dropped), then scores
   the top candidates with one Gemini Flash call (skipped when there is
   nothing new). The scoring prompt carries an explicit seniority
   calibration — a role aimed at a materially more experienced person is
   capped below 50 no matter how well the technologies line up.
5. Emails a top-10 digest to the owner and publishes `skills_graph.json`
   (including a cumulative "qualifying jobs discovered" counter) to the
   `agent-data` branch, which powers `skills.html`.

## Sources

Every source is an official, documented API. Nothing is scraped, and
LinkedIn / Indeed / ZipRecruiter are excluded on purpose — no usable public
API and hostile to automated access, so there is no polite way in.

`ats.py` is the primary source and the reason match quality is what it is:
Greenhouse, Lever, Ashby and SmartRecruiters publish each company's open
roles as structured JSON at a keyless endpoint, so employment type,
workplace type and (on Ashby and Lever) compensation arrive as real typed
fields straight from the employer, and closed roles disappear instead of
lingering.

Those endpoints are per-company, so `agent/boards.json` holds a registry of
board slugs — every slug in the seed set was verified to return a non-empty
public job list. The registry grows on its own: each run harvests ATS links
out of the aggregator results, so an Adzuna hit pointing at
`boards.greenhouse.io/acme` teaches the agent to poll Acme's board directly
from then on (`discovered_boards.json`). Boards are polled
least-recently-first, round-robined across the four ATSs, capped by
`ATS_BOARDS_PER_RUN`.

Workable is deliberately absent: its keyless widget endpoint returns empty
job lists and its SPI endpoint is 401-gated, so there is nothing to consume
without a partner key.

**Applied / rejected tracking (email-driven):** each digest card carries
"Mark applied" and "Not a fit" links that compose a self-addressed
`CA-APPLIED` / `CA-REJECTED` email containing the job id. The next run reads
unseen tagged messages over IMAP (same Gmail app password) and records them
(`feedback.py`, with `applied.py` / `rejected.py` as thin wrappers).

Each record stores **two** identifiers, because the job id is a hash of the
posting URL: on its own it lets a role re-posted at a new URL come straight
back. Alongside it goes an opaque `role_key` — a hash of normalized company
+ title, with level noise ("Senior", "II") stripped so a lightly-retitled
re-post still matches. Suppression happens at discovery, so a rejected role
never costs an embedding, an enrichment or a digest slot again.

The key is deliberately company **and** title, never company alone:
rejecting "Data Analyst at Acme" must not blacklist Acme. Only opaque ids,
hashes and dates are stored — agent-data is public, and nothing on the
website reveals application activity.

**Hard guardrails:** the agent never submits applications, creates accounts,
fills forms, or contacts anyone. Its only outputs are the email digest (to the
owner's own address) and commits to `agent-data`.

**Cost:** GitHub Actions minutes are free (public repo); embeddings are local;
Gemini usage is one small Flash call per run at most — within the API free
tier for a daily run, so typically $0, and never more than a few cents per
month.

## One-time setup (repo → Settings → Secrets and variables → Actions)

| Secret | Where to get it | Required? |
|---|---|---|
| `GEMINI_API_KEY` | aistudio.google.com → Get API key (free tier) | For real scoring/profiles (heuristic fallback without it) |
| `GMAIL_APP_PASSWORD` | Google Account → Security → 2-Step Verification → App passwords | For the email digest |
| `ADZUNA_APP_ID` + `ADZUNA_APP_KEY` | developer.adzuna.com (free) | Optional — adds general US listings |
| `USAJOBS_API_KEY` + `USAJOBS_USER_AGENT` | developer.usajobs.gov (free; user agent = your email) | Optional — adds federal remote roles |
| `JOOBLE_API_KEY` | jooble.org/api/about (free) | Optional — adds broad aggregation |
| `RAPIDAPI_KEY` | rapidapi.com → subscribe to JSearch (free tier) | Optional — wraps Google Jobs and many boards |

The ATS boards (the main source) need no key at all.

Missing secrets never break a run — the affected source/stage is skipped with
a log line.

## Local dry run

```bash
pip install -r agent/requirements.txt   # or just `requests` for a minimal run
python agent/main.py --dry-run          # no email; digest saved to state dir
```

State lives in `agent-state/` locally (gitignored) and on the `agent-data`
branch in CI. The workflow runs daily at 11:30 UTC, on every push to `main`,
and via manual dispatch (Actions → Career Agent → Run workflow).

## Extending

`agent/main.py` runs the stages in order against the shared state directory.
Future agents — resume refinement, cover-letter drafts (human-reviewed),
interview prep, company research, salary benchmarking, application tracking —
plug in as new stages or as separate consumers of the `agent-data` branch.

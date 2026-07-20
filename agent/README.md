# Career Agent

A scheduled pipeline (`.github/workflows/career-agent.yml`) that:

1. Indexes `resume.html` + GitHub repos (READMEs, topics, commits) into a
   structured skill profile — via one Gemini call, **only when the source
   material changes**.
2. Fetches remote job postings from free APIs: Remotive, RemoteOK, Arbeitnow,
   HN "Who is hiring" (keyless) + Adzuna, USAJobs (free keys) + the
   doomersareretardedcommunists.com daily snapshots (keyless; see below).
   **A stated salary is required**: postings must state compensation (in
   their salary field, or extractably in their text) and it must reach
   `MIN_SALARY_USD` ($130k, see `config.py`) — everything else is dropped. Skills matching
   `SKILL_BLOCKLIST_PATTERNS` (legacy SAS/Stata/NLP-BERT work) are stripped
   from the profile so they never influence matching or the graph.
3. Prefilters with local embeddings (fastembed ONNX — zero API cost),
   enriches finalists that lack posting text straight from the employer's
   ATS (`enrich.py`: Greenhouse/Lever/SmartRecruiters/Workday/Ashby public
   JSON endpoints; postings the ATS reports gone are dropped), then scores
   the top candidates with one Gemini Flash call (skipped when there is
   nothing new).
4. Emails a top-10 digest to the owner and publishes `skills_graph.json`
   (including a cumulative "qualifying jobs discovered" counter) to the
   `agent-data` branch, which powers `skills.html`.

**Applied tracking (email-driven):** each digest card has a "✓ Mark
applied" mailto link that composes a self-addressed `CA-APPLIED` email
containing the job id. The next run reads unseen `CA-APPLIED` messages over
IMAP (same Gmail app password) and records the ids in `applied.json`
(`applied.py`); applied jobs stop appearing in digests and in the public
graph. Only opaque ids + dates are stored — agent-data is public, and
nothing on the website reveals application activity.

**Doomers source etiquette:** doomersareretardedcommunists.com is a
community dashboard hosted at the author's own cost. It publishes immutable
daily JSON snapshots (manifest at `/data/current/manifest.json`). The
integration therefore checks only the tiny manifest each run and downloads
the large per-group files at most once per snapshot day, caching the
filtered parse in `doomers_cache.json`. Do not add page crawling or extra
groups without thinking about their bandwidth bill.

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

# Handoff — career agent

Written 2026-07-27 for a session picking this up cold. Read this before
touching `agent/`.

Companion docs: `agent/README.md` (how the pipeline works),
`agent/TRACKER_PLAN.md` (the work you are here to do).

---

## 1. What this is

A daily pipeline (`.github/workflows/career-agent.yml`, ~07:30 ET, also on
every push to `main`) that finds remote analytics/AI roles for the repo
owner, scores them against his resume, and emails a digest. State lives on
the `agent-data` branch; the public site `skills.html` reads it.

Owner context that drives every threshold: ~4 years' experience, currently
$95k in Raleigh NC, wants remote so he can move to Wrightsville, would
relocate for $300k+.

**Pipeline** (`main.py`, seven stages): profile from resume+GitHub → applied
/rejected email sync → discovery → company gate → embedding prefilter +
enrichment → LLM scoring → digest + graph export.

**Module map**

| File | Role |
|---|---|
| `ats.py` | Greenhouse/Lever/Ashby/SmartRecruiters boards. Primary source. |
| `fetch_jobs.py` | Aggregators + orchestration + the pay/location gate |
| `role_filter.py` | Discipline, seniority, full-time gates |
| `standing_answers.py` | Answers that are identical on every application form |
| `preflight.py` | Reads the application form before you fill it |
| `feedback.py` | Shared IMAP machinery; `applied.py`/`rejected.py` are wrappers |
| `score_llm.py` | One batched Gemini call, with seniority calibration |
| `digest.py` | The email |
| `boards.json` | 266 verified ATS board slugs; grows itself |

---

## 2. Where things stand

Last verified run: **227 live jobs** — 113 remote with stated pay, 89
relocation-grade on-site (≥$300k), 31 pay-undisclosed provisional. Scoring is
real Gemini at 0.85–0.95 confidence. Digest is 15 roles. Pre-flight covers
Greenhouse only (146 of 266 boards).

Started at 51 jobs with heuristic-only scoring, so treat that number as the
health check: a large drop means something upstream broke silently.

**Secrets** (repo → Settings → Secrets): `GEMINI_API_KEY`,
`GMAIL_APP_PASSWORD`, `ADZUNA_APP_ID`, `ADZUNA_API_KEY`, `USAJOBS_API_KEY`,
`USAJOBS_USER_AGENT`, `RAPIDAPI_KEY`. Jooble is unset and skips cleanly.

---

## 3. Your task

`agent/TRACKER_PLAN.md` has the full design. Decisions are **already made** —
do not reopen them:

- Public site, **private** application history
- Write path is **batched email** (`CA-STATUS`), not a backend
- First version is the **full pipeline board**

Phase 0 is partly done. What remains, in order:

1. **Finish the privacy split.** Move state to a private repo
   (`contents:write` fine-grained PAT; the default `GITHUB_TOKEN` cannot
   write cross-repo). `agent-data` keeps only `skills_graph.json`,
   `profile.json`, `totals.json` — everything `skills.html` reads.
2. **`pipeline.py`** — status set, legal transitions, `pipeline.json`, and the
   `CA-STATUS` protocol parsed through `feedback.py`.
3. **`tracker.py`** — renders a self-contained `tracker.html` with data
   embedded (avoids `file://` fetch restrictions; no local server needed).
   Kanban, company-level dedupe, stale-application nudges, notes, pre-flight
   questions inline.
4. **Conversion analytics** feeding back into ranking.

---

## 4. Invariants — these break silently

Every one of these was a real bug found by running against live data.

**Fit and pipeline are different axes.** `rejected.json` means *"I don't want
this"* and trains the negative centroid in `rank.py`. An employer's rejection
must NEVER write there, or the ranker learns to avoid the roles he most
wanted. This is the single most important rule in the tracker work.

**`agent-data` is public.** Assume anything committed there is readable by his
employer.

**Never run `salary_max_usd()` on prose.** It returns the largest number it
finds, so "500,000 customers" reads as a $500k salary. Long text goes through
`find_salary_snippet()` first, which validates the figure looks like pay.

**`us_friendly()` and `looks_us()` are different.** The first only recognises
remote-style strings ("Remote - US"); the second reads real locations ("San
Francisco, CA"). Using the wrong one silently discarded every on-site US role.

**Watch positional booleans.** `_from_ats()` passed `remote=True` as a literal,
harmless until on-site roles were allowed — then they arrived labelled remote
and were judged against the wrong salary bar.

**Bump `preflight.ANALYSIS_VERSION` whenever blocker rules change**, or cached
verdicts replay the old rules forever.

**Gemini `max_tokens` must scale with batch size.** A flat 3500 truncated a
60-job array mid-object and silently dumped every score to the heuristic
fallback. `_extract_json_array` now salvages partial responses — keep that.

**`SEARCH_QUERIES` is not `profile["target_titles"]`.** The LLM writes
aspirational titles ("Senior Business Analytics Engineer") that match nothing
in a job index. Keyword sources use the canonical list in `config.py`;
target_titles is for embedding similarity only.

**Standing answers must resolve against the field's real options.** A select
only accepts its own values. Where nothing is true — a "How did you hear about
us?" dropdown of Career Page / LinkedIn / Indeed — hand the question back.
Never pick a near-miss; that is a lie on an application.

**Never auto-answer** consent/arbitration acknowledgements, EEO, demographics,
pronouns, or "how do you use AI tools". See `standing_answers.NEVER_ANSWER`.

**Heuristic scores carry no `seniority_fit`**, so pay-undisclosed roles fail
closed without a working LLM. That is deliberate. The `MIN_SKILL_MATCHES` gate
likewise exempts heuristic scores, or it empties every digest.

**JSearch**: endpoint is `/search-v2` (`/search` is retired and 404s);
`data` is an object holding `jobs` + `cursor`, not an array; skipped on
push-triggered runs to protect a 200/month budget.

**The digest renders in a mail client.** Tables for layout, inline styles, no
flex/grid. There is one `<style>` block and it carries only dark-mode and
narrow-screen rules.

---

## 5. Running it

```bash
pip install -r agent/requirements.txt
python agent/main.py --dry-run --state-dir /tmp/state   # no email sent
```

Without `GEMINI_API_KEY` locally you get heuristic scores at 0.30 confidence —
expected, not a bug. `fastembed` is optional locally; it falls back to keyword
ranking.

CI: `gh workflow run career-agent.yml --ref main`, then
`gh run view <id> --log`. A push to `main` also triggers a run (and skips
JSearch).

Verify a change end to end by reading the log's funnel counts, then
`git show origin/agent-data:last_digest.html`.

---

## 6. Open items

- **`agent-data` git history still holds the stripped plaintext.** The current
  tip is clean, but 64 historical commits contain title/company/url beside the
  applied ids. The branch is machine-generated, so squashing it to a single
  orphan commit is safe and would erase the exposure. Not yet done — ask
  before force-pushing.
- **Job ids are `sha1(posting URL)`.** Even with plaintext gone, re-fetching
  the same public boards and hashing tests membership in `applied.json`.
  Salting the id with an Actions secret would close it but invalidates all
  existing state. Phase 0's private repo is the better answer.
- **Pre-flight is Greenhouse-only.** Ashby exposes an `applicationForm` field
  (confirmed to exist; the GraphQL subfield names were guessed wrong and need
  an introspection pass). That would add ~92 boards.
- **USAJobs authenticates but returns nothing** — genuinely no federal remote
  full-time roles at these titles. Leave it wired.
- **`MIN_SKILL_MATCHES = 3`** against a 13–15 skill profile is strict; it has
  swung the digest pool between 10 and 63 candidates. A knob to watch.
- **`doomers_cache.json`** is still on `agent-data` from a removed source and
  can be deleted.
- **Resume gaps** the owner still owes numbers for: Document Sourcing
  Automation (file count / hours saved), HELOC print-workflow savings, and the
  transaction volume behind "Production ML Serving".

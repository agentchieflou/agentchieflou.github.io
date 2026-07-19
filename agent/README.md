# Career Agent

A scheduled pipeline (`.github/workflows/career-agent.yml`) that:

1. Indexes `resume.html` + GitHub repos (READMEs, topics, commits) into a
   structured skill profile — via one Claude call, **only when the source
   material changes**.
2. Fetches remote job postings from free APIs: Remotive, RemoteOK, Arbeitnow,
   HN "Who is hiring" (keyless) + Adzuna, USAJobs (free keys).
3. Prefilters with local embeddings (fastembed ONNX — zero API cost), then
   scores the top candidates with one Claude Haiku call (skipped when there is
   nothing new).
4. Emails a top-5 digest to the owner and publishes `skills_graph.json` to the
   `agent-data` branch, which powers `skills.html`.

**Hard guardrails:** the agent never submits applications, creates accounts,
fills forms, or contacts anyone. Its only outputs are the email digest (to the
owner's own address) and commits to `agent-data`.

**Cost:** GitHub Actions minutes are free (public repo); embeddings are local;
Claude usage is one small Haiku call per run at most — typically a few cents
per month, $0 on days with nothing new to score.

## One-time setup (repo → Settings → Secrets and variables → Actions)

| Secret | Where to get it | Required? |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API keys | For real scoring/profiles (heuristic fallback without it) |
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

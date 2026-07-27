# Application tracker — design plan

Status: **planning**. Nothing here is built yet.

Goal: know at a glance who has been applied to, who hasn't, what stage each
application is at, and which ones have gone quiet.

---

## 0. The leak that shapes everything

`applied.py` and `rejected.py` both claim they store "only opaque job ids and
dates — never titles, companies, or URLs" and that "nothing on the website
reveals application activity."

That is false today. `seen_jobs.json` sits on the same public branch and maps
every id to its title, company and URL:

```
applied.json    9 opaque sha1 ids
seen_jobs.json  1292 entries, fields: first_seen, last_seen, content_hash,
                title, company, source, url
join            9 of 9 resolve
```

Anyone can read the full application history. A tracker multiplies the
exposure, so the split has to come first.

**Public** (what `skills.html` actually reads): `skills_graph.json`,
`profile.json`, `totals.json`.

**Private** (everything else): `applied.json`, `rejected.json`,
`seen_jobs.json`, `scores.json`, `preflight_cache.json`, `company_cache.json`,
`enrich_cache.json`, `embeddings/`, `displayed_history.json`,
`discovered_boards.json`, `board_poll.json`, `last_digest.html`.

`graph_export.py` already excludes applied and rejected jobs from the public
graph, so the public site loses nothing.

---

## 1. Two axes, never one

`rejected.json` currently means *"I don't want this"* — the owner's judgment.
A tracker also needs *"they turned me down"*. These must stay separate stores.

| Axis | Values | Feeds `rank.py` negative centroid? |
|---|---|---|
| **Fit** (owner's call) | `interested`, `not-a-fit` | **Yes** — this is the training signal |
| **Pipeline** (employer's) | `surfaced` → `prepped` → `applied` → `screening` → `interviewing` → `offer` / `closed-rejected` / `closed-silent` | **No** |

Merging them would be actively harmful: an employer rejection would teach the
ranker to avoid exactly the roles the owner wanted most.

---

## 2. Architecture

Constraints inherited from the rest of the system: no backend, no secrets in a
browser, runs at cents/month, email is the feedback bus.

Private history rules out a hosted page — a static site cannot read a private
store without auth. So the board is **generated, not served**:

```
run N     agent writes tracker.html with all data embedded  ->  private state
you       open the file, click through statuses             ->  localStorage
you       press "sync"  ->  one CA-STATUS email, all changes batched
run N+1   IMAP sync ingests it, commits, regenerates the board
```

Self-contained output avoids `file://` fetch being blocked, needs no local
server, and is private by construction. The trade: updates land on the next
run, and `localStorage` covers the gap in the meantime.

If a genuinely hosted, always-live board is wanted later, that requires a
backend with auth (Cloudflare Worker + KV would do it on a free tier) — a
deliberate departure from how everything else here works, worth doing only
once the data model has proven itself.

---

## 3. Phases

### Phase 0 — stop the leak
- Private repo (e.g. `career-agent-state`) for state; workflow pushes with a
  fine-grained PAT (`contents:write` on that repo only), since the default
  `GITHUB_TOKEN` cannot write across repositories.
- `agent-data` keeps only the three public files.
- Correct the privacy claims in `applied.py` / `rejected.py` docstrings.

### Phase 1 — pipeline state
- `pipeline.py`: status set, legal transitions, `pipeline.json`.
- `CA-STATUS` email protocol, parsed by the existing `feedback.py` machinery:
  ```
  status <job_id> applied
  status <job_id> screening
  note   <job_id> recruiter said Q3 headcount
  ```
- Digest cards gain the intermediate actions; `not-a-fit` still routes to
  `rejected.json` so the ranker keeps learning.

### Phase 2 — the board
- `tracker.py` renders a self-contained `tracker.html` each run.
- Columns across the pipeline; cards carry score, source, comp, pre-flight
  questions and notes.
- **Company-level dedupe** — "have I already applied here?" is the question
  the current setup cannot answer at all.
- **Stale nudges** — applied > N days with no movement is the most actionable
  view in the whole thing.
- Batched sync button: builds one `mailto:` from every pending change.

### Phase 3 — close the loop
- Conversion rates by role family, score band, source, and comp band.
- Feed measured conversion back into ranking, so the agent learns what
  actually converts rather than what merely looks similar.

---

## 4. Known risks

- **`mailto:` length.** ~2000 chars is the practical ceiling in some clients.
  Roughly 40 chars per change, so ~40 changes per email. Chunk beyond that, or
  fall back to a copy-to-clipboard block.
- **PAT scope.** A cross-repo token is a real secret with write access. Scope
  it to the one repo, nothing else.
- **Divergence.** `localStorage` can drift from committed state across
  devices. Committed state must always win on reload.
- **Embedded size.** 1292 seen jobs plus scores is on the order of 1–2 MB
  inlined. Fine for a local file; would need trimming if ever hosted.

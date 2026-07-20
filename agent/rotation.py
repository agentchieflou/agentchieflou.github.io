"""Freshness rotation for the displayed job pool (skills.html graph/matrix/
ledger). Scaling the pool to TOP_N_GRAPH_JOBS on its own would let the same
top-scoring jobs calcify at the front run after run — this guarantees the
pool keeps turning over: each run, at least MIN_NEW_PER_RUN slots go to jobs
that weren't displayed last run, freed up by evicting the stalest
currently-displayed jobs first (not the lowest-scoring ones — a job that's
been shown for weeks makes room before a job shown yesterday, regardless of
score, as long as both still clear every quality gate).

`ranked` must already be fully gated (salary/skill-match/remote/accessible/
not-applied/not-rejected/not-dead) and scored — this module only decides
*which* qualifying jobs make the cut, never re-evaluates quality.
"""
import datetime as dt

from config import STATE_DIR, MIN_NEW_PER_RUN, TOP_N_GRAPH_JOBS
from digest import combined_score
from util import load_json, log, save_json

HISTORY_PATH = STATE_DIR / "displayed_history.json"


def select_display_set(ranked, scores, target=TOP_N_GRAPH_JOBS, min_new=MIN_NEW_PER_RUN):
    """Returns up to `target` jobs from `ranked`, sorted by score desc, with
    a freshness quota enforced. Persists the updated display history."""
    history = load_json(HISTORY_PATH, {})
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()

    scored_ranked = [j for j in ranked if j["id"] in scores]
    scored_ranked.sort(key=lambda j: -combined_score(scores[j["id"]]))

    carried = [j for j in scored_ranked if j["id"] in history]
    fresh = [j for j in scored_ranked if j["id"] not in history]

    # Carried jobs are evicted stalest-first to free room, but the room
    # reserved for "new" never shrinks below min_new just because there
    # happens to be a lot of qualifying carryover this run.
    room_for_new = min(len(fresh), max(min_new, target - len(carried)))
    new_pick = fresh[:room_for_new]
    keep_n = max(0, target - len(new_pick))

    if len(carried) > keep_n:
        carried_by_age = sorted(carried, key=lambda j: history.get(j["id"], now_iso))
        evict_ids = {j["id"] for j in carried_by_age[:len(carried) - keep_n]}
        carried = [j for j in carried if j["id"] not in evict_ids]

    display = carried + new_pick
    display.sort(key=lambda j: -combined_score(scores[j["id"]]))

    kept_ids = {j["id"] for j in display}
    new_history = {jid: history[jid] for jid in kept_ids if jid in history}
    for j in display:
        new_history.setdefault(j["id"], now_iso)
    save_json(HISTORY_PATH, new_history)

    log.info("display rotation: %d carried, %d new (target %d, min_new %d)",
             len(carried), len(new_pick), target, min_new)
    if len(new_pick) < min_new:
        log.info("display rotation: only %d new qualifying jobs available "
                 "this run (wanted %d)", len(new_pick), min_new)
    return display

"""Career agent pipeline entry point.

Stages run in order against the shared state directory; each is idempotent,
so a partially-failed run is safe to retry. Future agents (resume refinement,
cover-letter drafts, interview prep, ...) plug in as additional stages here
or as separate consumers of the agent-data branch.

Usage:
  python agent/main.py             # full run (emails if GMAIL_APP_PASSWORD set)
  python agent/main.py --dry-run   # no email; digest written to state dir
"""
import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from util import log


def main():
    parser = argparse.ArgumentParser(description="Run the career agent pipeline")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch, rank and score, but do not send email")
    parser.add_argument("--state-dir", help="override state directory")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.state_dir:
        # Must happen before the stage modules are imported — they resolve
        # their state file paths from config.STATE_DIR at import time.
        config.STATE_DIR = Path(args.state_dir)

    import applied as applied_mod
    import company_enrich
    import digest as digest_mod
    import enrich
    import fetch_jobs
    import graph_export
    import index_profile
    import rank
    import rejected as rejected_mod
    import score_llm

    config.STATE_DIR.mkdir(parents=True, exist_ok=True)

    log.info("stage 1/7: profile indexing")
    profile, changed, note = index_profile.build_profile()
    log.info("profile version %s (%s skills, changed=%s)",
             profile["version"], len(profile.get("skills", [])), changed)

    log.info("stage 2/7: applied + rejected email sync")
    applied = applied_mod.sync_from_inbox()
    rejected = rejected_mod.sync_from_inbox()

    log.info("stage 3/7: job discovery")
    jobs, new_ids, expired, totals = fetch_jobs.fetch_all(profile.get("target_titles", []))
    log.info("%d live jobs, %d new, %d expired, %d discovered all-time",
             len(jobs), len(new_ids), expired, totals["all_time"])

    log.info("stage 4/7: company accessibility gate")
    before_company_gate = len(jobs)
    jobs = company_enrich.gate_jobs(jobs)
    log.info("company gate: %d of %d jobs kept", len(jobs), before_company_gate)

    log.info("stage 5/7: embedding prefilter + finalist enrichment")
    rank.snapshot_rejected_vectors(jobs, set(rejected))
    ranked = rank.rank_jobs(profile, jobs)
    enrich.enrich_candidates(ranked[:config.MAX_LLM_CANDIDATES])
    ranked = [j for j in ranked if not j.get("dead")]

    log.info("stage 6/7: LLM scoring")
    scores, newly_scored = score_llm.score_candidates(profile, ranked)
    log.info("%d newly scored, %d total scored", newly_scored, len(scores))

    # A job must clear the minimum skill-connection bar to count as a real
    # match anywhere downstream — a 1-2 skill overlap isn't "worth applying to".
    before_gate = len(ranked)
    ranked = [j for j in ranked if len(scores.get(j["id"], {}).get("matched_skills", [])) >= config.MIN_SKILL_MATCHES]
    log.info("skill-match gate: %d of %d scored jobs kept (>= %d matched skills)",
             len(ranked), before_gate, config.MIN_SKILL_MATCHES)

    log.info("stage 7/7: digest + graph export")
    exclude = set(applied) | set(rejected)
    top = digest_mod.pick_top(ranked, scores, exclude=exclude)
    stats = {
        "date": dt.date.today().isoformat(),
        "evaluated": len(jobs),
        "sources": len(fetch_jobs.SOURCES),
        "new": len(new_ids),
        "expired": expired,
        "profile_note": note,
        "new_ids": new_ids,
        "applied_total": len(applied),
        "rejected_total": len(rejected),
        "discovered_total": totals["all_time"],
    }
    digest_mod.send_digest(top, stats, dry_run=args.dry_run)
    # Applied and rejected jobs are kept out of the public graph as well
    graph_export.export_graph(profile, [j for j in ranked if j["id"] not in exclude],
                              scores, totals)

    for i, (j, s) in enumerate(top, 1):
        log.info("top %d: %s @ %s (score %d, conf %.2f)",
                 i, j["title"], j["company"], s["match_score"], s["confidence"])
    log.info("done")


if __name__ == "__main__":
    main()

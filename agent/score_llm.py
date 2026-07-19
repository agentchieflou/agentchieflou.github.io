"""Gemini scoring of prefiltered candidates.

Exactly one generateContent call per run, and only when there are new
candidates: scores are cached by job id + profile version, so a job is
scored once per profile revision. Descriptions are truncated before
prompting. Without an API key a heuristic score derived from the embedding
prefilter is stored so the digest still ships.
"""
import datetime as dt
import json
import re

import llm
from config import (DESC_TRUNCATE, GEMINI_API_KEY,
                    MAX_LLM_CANDIDATES, STATE_DIR)
from util import load_json, log, save_json

SCORES_PATH = STATE_DIR / "scores.json"


def _extract_json_array(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE)
    start = text.find("[")
    if start == -1:
        raise ValueError("no JSON array in response")
    end = text.rfind("]")
    if end == -1 or end < start:
        raise ValueError("no closing bracket found in response")
    return json.loads(text[start:end + 1])


def _gemini_score(profile, candidates):
    skill_names = [s["name"] for s in profile.get("skills", [])]
    jobs_payload = [{
        "id": j["id"],
        "title": j["title"],
        "company": j["company"],
        "location": j["location"],
        "salary": j.get("salary"),
        "description": j["description"][:DESC_TRUNCATE],
    } for j in candidates]
    prompt = (
        "You are a career-matching analyst. Score how well each job posting fits this "
        "candidate. Consider technology-stack similarity, demonstrated project experience, "
        "seniority/career progression, growth opportunity, and compensation when stated. "
        "The candidate only wants fully-remote roles.\n\n"
        f"CANDIDATE SUMMARY:\n{profile.get('summary', '')}\n\n"
        f"CANDIDATE SKILLS: {json.dumps(skill_names)}\n\n"
        f"TARGET TITLES: {json.dumps(profile.get('target_titles', []))}\n\n"
        f"JOBS:\n{json.dumps(jobs_payload, ensure_ascii=False)}\n\n"
        "Respond with ONLY a JSON array, one object per job, keys:\n"
        '{"id": str, "match_score": int 0-100, "confidence": float 0-1,\n'
        ' "why": "<=2 sentences on why it fits", "matched_skills": [names drawn ONLY '
        "from CANDIDATE SKILLS], \"missing_skills\": [skills the posting wants that the "
        'candidate lacks], "resume_suggestions": [0-2 short concrete resume tweaks]}'
    )
    return _extract_json_array(llm.generate(prompt, max_tokens=3000))


def _heuristic_score(candidates):
    return [{
        "id": j["id"],
        "match_score": int(min(1.0, j.get("prefilter", 0) * 1.4) * 100),
        "confidence": 0.3,
        "why": "Heuristic score (no GEMINI_API_KEY set): ranked by embedding "
               "similarity to your profile.",
        "matched_skills": [], "missing_skills": [], "resume_suggestions": [],
        "heuristic": True,
    } for j in candidates]


def score_candidates(profile, ranked_jobs):
    """Scores unscored top candidates; returns (scores_dict, n_newly_scored)."""
    scores = load_json(SCORES_PATH, {})
    version = profile["version"]

    def needs_scoring(j):
        s = scores.get(j["id"])
        if not s or s.get("profile_version") != version:
            return True
        # Upgrade heuristic scores to real ones once an API key is available
        return bool(s.get("heuristic")) and bool(GEMINI_API_KEY)

    # Only the prefiltered top is ever sent to the LLM — on a typical day with
    # no new strong candidates this list is empty and no API call is made.
    candidates = [j for j in ranked_jobs[:MAX_LLM_CANDIDATES] if needs_scoring(j)]
    if not candidates:
        log.info("no new candidates to score - skipping Gemini call")
        return scores, 0

    if GEMINI_API_KEY:
        try:
            results = _gemini_score(profile, candidates)
        except Exception as e:
            log.warning("Gemini scoring failed, storing heuristic scores: %s", e)
            results = _heuristic_score(candidates)
    else:
        results = _heuristic_score(candidates)

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    by_id = {j["id"]: j for j in candidates}
    n = 0
    for r in results:
        jid = r.get("id")
        if jid not in by_id:
            continue
        scores[jid] = {
            "profile_version": version,
            "match_score": max(0, min(100, int(r.get("match_score", 0)))),
            "confidence": max(0.0, min(1.0, float(r.get("confidence", 0)))),
            "why": str(r.get("why", ""))[:500],
            "matched_skills": [str(s)[:60] for s in r.get("matched_skills", [])][:10],
            "missing_skills": [str(s)[:60] for s in r.get("missing_skills", [])][:8],
            "resume_suggestions": [str(s)[:200] for s in r.get("resume_suggestions", [])][:2],
            "prefilter": by_id[jid].get("prefilter", 0),
            "heuristic": bool(r.get("heuristic")),
            "scored_at": now,
        }
        n += 1

    # Drop scores for jobs that no longer exist anywhere
    live = {j["id"] for j in ranked_jobs}
    scores = {jid: s for jid, s in scores.items() if jid in live}
    save_json(SCORES_PATH, scores)
    return scores, n

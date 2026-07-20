"""Exports skills_graph.json for the site's skills.html visualization.

Shape:
  skills: owned skills (center cluster) — size from strength
  jobs:   current top matches (outer ring) — size from match score
  edges:  job<->skill links; `missing: true` edges point at ghost skill
          nodes (skills the market wants that the profile lacks)
"""
import datetime as dt

from company_enrich import CACHE_PATH as COMPANY_CACHE_PATH
from config import STATE_DIR
from rotation import select_display_set
from util import load_json, norm_key, save_json

GRAPH_PATH = STATE_DIR / "skills_graph.json"


def export_graph(profile, jobs, scores, totals=None):
    skills = [{
        "id": norm_key(s["name"]),
        "name": s["name"],
        "category": s.get("category", "tools"),
        "strength": round(float(s.get("strength", 0.5)), 3),
    } for s in profile.get("skills", [])]
    skill_ids = {s["id"] for s in skills}

    company_cache = load_json(COMPANY_CACHE_PATH, {})
    display = select_display_set(jobs, scores)

    job_nodes, edges, ghosts = [], [], {}
    for j in display:
        s = scores[j["id"]]
        company = company_cache.get(norm_key(j["company"])) or {}
        job_nodes.append({
            "id": j["id"],
            "title": j["title"],
            "company": j["company"],
            "sector": s.get("sector") or company.get("sector") or "Other",
            "company_description": (company.get("description") or "")[:300] or None,
            "location": j["location"],
            "salary": j.get("salary"),
            "url": j["url"],
            "source": j["source"],
            "score": s["match_score"],
            "confidence": round(s["confidence"], 2),
            "why": s["why"],
        })
        matched = s.get("matched_skills") or []
        for rank, name in enumerate(matched):
            sid = norm_key(name)
            if sid in skill_ids:
                # Earlier in the list = more central to the posting
                weight = round(max(0.35, 1.0 - rank * 0.12), 2)
                edges.append({"skill": sid, "job": j["id"], "weight": weight, "missing": False})
        for name in s.get("missing_skills") or []:
            gid = "ghost-" + norm_key(name)
            ghosts.setdefault(gid, {"id": gid, "name": name, "ghost": True})
            edges.append({"skill": gid, "job": j["id"], "weight": 0.5, "missing": True})

    graph = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "profile_version": profile["version"],
        # Cumulative discovery counter for the site — displayed jobs are
        # capped, but every qualifying job ever found counts here. Applied
        # status is deliberately never exported.
        "totals": {"discovered_all_time": (totals or {}).get("all_time", 0),
                   "live": (totals or {}).get("live", len(jobs))},
        "skills": skills,
        "ghost_skills": list(ghosts.values()),
        "jobs": job_nodes,
        "edges": edges,
    }
    save_json(GRAPH_PATH, graph)
    return graph

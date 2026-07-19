"""Exports skills_graph.json for the site's skills.html visualization.

Shape:
  skills: owned skills (center cluster) — size from strength
  jobs:   current top matches (outer ring) — size from match score
  edges:  job<->skill links; `missing: true` edges point at ghost skill
          nodes (skills the market wants that the profile lacks)
"""
import datetime as dt

from config import STATE_DIR, TOP_N_GRAPH_JOBS
from digest import combined_score
from util import norm_key, save_json

GRAPH_PATH = STATE_DIR / "skills_graph.json"


def export_graph(profile, jobs, scores):
    skills = [{
        "id": norm_key(s["name"]),
        "name": s["name"],
        "category": s.get("category", "tools"),
        "strength": round(float(s.get("strength", 0.5)), 3),
    } for s in profile.get("skills", [])]
    skill_ids = {s["id"] for s in skills}

    scored = [(j, scores[j["id"]]) for j in jobs if j["id"] in scores]
    scored.sort(key=lambda js: -combined_score(js[1]))
    scored = scored[:TOP_N_GRAPH_JOBS]

    job_nodes, edges, ghosts = [], [], {}
    for j, s in scored:
        job_nodes.append({
            "id": j["id"],
            "title": j["title"],
            "company": j["company"],
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
        "skills": skills,
        "ghost_skills": list(ghosts.values()),
        "jobs": job_nodes,
        "edges": edges,
    }
    save_json(GRAPH_PATH, graph)
    return graph

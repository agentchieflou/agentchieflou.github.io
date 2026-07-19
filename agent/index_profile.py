"""Builds the skill profile from the resume + GitHub activity.

Cost controls:
- GitHub API calls use ETag conditional requests (304s are free against the
  rate limit and skip re-downloading bodies).
- The Gemini extraction call runs ONLY when the hashed source material
  (resume text + repo metadata/READMEs/commits) has changed since last run.
- Without a GEMINI_API_KEY (or on API failure) a heuristic profile is
  derived from repo languages/topics so the pipeline still completes.
"""
import datetime as dt
import json
import re

import requests

import llm
from config import (GEMINI_API_KEY, GITHUB_TOKEN, GITHUB_USER,
                    REPO_ROOT, STATE_DIR, USER_AGENT)
from util import html_to_text, load_json, log, save_json, sha256

PROFILE_PATH = STATE_DIR / "profile.json"
HTTP_CACHE_PATH = STATE_DIR / "http_cache.json"

DEFAULT_TITLES = [
    "Business Analyst", "Data Analyst", "Business Intelligence Analyst",
    "Analytics Consultant", "Business Analytics Consultant",
]


def _gh_session():
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    s.headers["Accept"] = "application/vnd.github+json"
    if GITHUB_TOKEN:
        s.headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return s


def _cached_get(session, cache, url, accept=None):
    """GET with ETag revalidation; returns response text or None."""
    entry = cache.get(url, {})
    headers = {}
    if accept:
        headers["Accept"] = accept
    if entry.get("etag"):
        headers["If-None-Match"] = entry["etag"]
    try:
        r = session.get(url, headers=headers, timeout=30)
    except requests.RequestException as e:
        log.warning("GitHub fetch failed %s: %s", url, e)
        return entry.get("body")
    if r.status_code == 304:
        return entry.get("body")
    if r.status_code != 200:
        log.warning("GitHub fetch %s -> %s", url, r.status_code)
        return entry.get("body")
    cache[url] = {"etag": r.headers.get("ETag"), "body": r.text}
    return r.text


def gather_sources():
    """Returns (source_text, repo_summaries). source_text feeds the hash."""
    resume_html = (REPO_ROOT / "resume.html").read_text(encoding="utf-8")
    resume_text = html_to_text(resume_html, skip_classes={"kate-only"})

    cache = load_json(HTTP_CACHE_PATH, {})
    s = _gh_session()
    repos_raw = _cached_get(s, cache, f"https://api.github.com/users/{GITHUB_USER}/repos?per_page=100&sort=updated")
    repos = []
    if repos_raw:
        try:
            repos = [r for r in json.loads(repos_raw) if not r.get("fork")]
        except json.JSONDecodeError:
            pass

    summaries = []
    for r in repos:
        full = r["full_name"]
        readme = _cached_get(s, cache, f"https://api.github.com/repos/{full}/readme",
                             accept="application/vnd.github.raw+json") or ""
        commits_raw = _cached_get(s, cache, f"https://api.github.com/repos/{full}/commits?per_page=10") or "[]"
        try:
            commit_subjects = [c["commit"]["message"].splitlines()[0] for c in json.loads(commits_raw)]
        except (json.JSONDecodeError, KeyError, TypeError):
            commit_subjects = []
        summaries.append({
            "name": r["name"],
            "description": r.get("description") or "",
            "language": r.get("language") or "",
            "topics": r.get("topics") or [],
            "readme_excerpt": readme[:1500],
            "recent_commits": commit_subjects,
        })
    save_json(HTTP_CACHE_PATH, cache)

    source_text = resume_text + "\n\n" + json.dumps(summaries, sort_keys=True)
    return source_text, resume_text, summaries


def _extract_json(text):
    """Pull the first JSON object out of a model response."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE)
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in response")
    return json.loads(text[start:text.rfind("}") + 1])


def _gemini_profile(resume_text, repo_summaries):
    prompt = (
        "Analyze this person's resume and GitHub portfolio and produce a structured "
        "skill profile as JSON with exactly these keys:\n"
        '{"summary": "<3-4 sentence professional summary>",\n'
        ' "skills": [{"name": str, "category": "analytics"|"engineering"|"tools"|"domain",\n'
        '             "strength": float 0-1 based on depth of evidence, "evidence": [str, ...]}],\n'
        ' "target_titles": [str, ...]}\n\n'
        "Rules: 12-25 skills; strength must reflect actual evidence (years, projects, "
        "repo activity), not resume keywords alone; target_titles = 4-6 job titles this "
        "person is genuinely competitive for today, ordered by fit. The person only "
        "wants fully-remote roles. Respond with JSON only.\n\n"
        f"RESUME:\n{resume_text[:6000]}\n\nGITHUB PORTFOLIO:\n"
        f"{json.dumps(repo_summaries, ensure_ascii=False)[:8000]}"
    )
    return _extract_json(llm.generate(prompt, max_tokens=2500))


def _heuristic_profile(resume_text, repo_summaries):
    """No-LLM fallback: languages/topics as skills, default target titles."""
    counts = {}
    for r in repo_summaries:
        if r["language"]:
            counts[r["language"]] = counts.get(r["language"], 0) + 1
        for t in r["topics"]:
            counts[t] = counts.get(t, 0) + 1
    for kw in ["SQL", "Python", "Tableau", "Power BI", "Excel", "R", "Snowflake",
               "Alteryx", "SAS", "ETL", "Agile", "RAG", "machine learning"]:
        if re.search(re.escape(kw), resume_text, re.IGNORECASE):
            counts[kw] = counts.get(kw, 0) + 2
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:20]
    mx = max((c for _, c in top), default=1)
    return {
        "summary": resume_text[:400],
        "skills": [{"name": k, "category": "tools", "strength": round(0.3 + 0.7 * c / mx, 2),
                    "evidence": []} for k, c in top],
        "target_titles": DEFAULT_TITLES,
    }


def build_profile():
    """Returns (profile_dict, changed: bool, change_note: str)."""
    source_text, resume_text, repo_summaries = gather_sources()
    version = sha256(source_text)[:12]
    existing = load_json(PROFILE_PATH, None)
    if existing and existing.get("version") == version:
        return existing, False, ""
    # A heuristic profile carries a "-h" version suffix so that once a working
    # GEMINI_API_KEY exists it gets re-extracted (and downstream embedding/
    # score caches keyed by version invalidate) even with unchanged sources.
    if existing and existing.get("version") == version + "-h" and not GEMINI_API_KEY:
        return existing, False, ""

    profile = None
    if GEMINI_API_KEY:
        try:
            profile = _gemini_profile(resume_text, repo_summaries)
        except Exception as e:
            log.warning("Gemini profile extraction failed, using heuristic: %s", e)
    if profile is None:
        profile = _heuristic_profile(resume_text, repo_summaries)
        profile["heuristic"] = True
        version += "-h"

    profile["version"] = version
    profile["remote_only"] = True
    profile["generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

    note = ""
    if existing:
        old = {s["name"].lower() for s in existing.get("skills", [])}
        new = {s["name"].lower() for s in profile.get("skills", [])}
        added, removed = sorted(new - old), sorted(old - new)
        bits = []
        if added:
            bits.append("new skills detected: " + ", ".join(added))
        if removed:
            bits.append("no longer emphasized: " + ", ".join(removed))
        note = "; ".join(bits) or "profile re-derived (source material changed)"
    save_json(PROFILE_PATH, profile)
    return profile, True, note

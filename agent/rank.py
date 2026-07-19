"""Embedding-based prefilter: cheap local vectors, no API cost.

Uses fastembed (ONNX, no torch) with a small English model. Every embedding
is cached by content hash, so a posting is embedded exactly once in its
lifetime and the profile is re-embedded only when it changes. If fastembed is
unavailable the ranker falls back to keyword overlap so the pipeline still
completes.
"""
import math
import os

from config import EMBED_MODEL, STATE_DIR
from util import load_json, log, norm_key, save_json

JOB_VEC_PATH = STATE_DIR / "embeddings" / "jobs.json"
PROFILE_VEC_PATH = STATE_DIR / "embeddings" / "profile.json"

_embedder = None
_embedder_failed = False


def _get_embedder():
    global _embedder, _embedder_failed
    if _embedder is not None or _embedder_failed:
        return _embedder
    try:
        from fastembed import TextEmbedding
        cache_dir = os.environ.get("FASTEMBED_CACHE_DIR")
        if cache_dir:
            cache_dir = os.path.expanduser(cache_dir)
        _embedder = TextEmbedding(model_name=EMBED_MODEL,
                                  **({"cache_dir": cache_dir} if cache_dir else {}))
    except Exception as e:
        log.warning("fastembed unavailable (%s) - falling back to keyword ranking", e)
        _embedder_failed = True
    return _embedder


def _embed(texts):
    emb = _get_embedder()
    if emb is None:
        return None
    return [[float(x) for x in v] for v in emb.embed(texts)]


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _profile_doc(profile):
    skills = ", ".join(f"{s['name']}" for s in profile.get("skills", []))
    return (f"{profile.get('summary', '')}\nSkills: {skills}\n"
            f"Target roles: {', '.join(profile.get('target_titles', []))}")


def _profile_vectors(profile):
    """Profile doc + each target title, re-embedded only on version change."""
    cached = load_json(PROFILE_VEC_PATH, {})
    if cached.get("version") == profile["version"]:
        return cached["doc"], cached["titles"]
    texts = [_profile_doc(profile)] + list(profile.get("target_titles", []))
    vecs = _embed(texts)
    if vecs is None:
        return None, None
    doc, titles = vecs[0], vecs[1:]
    save_json(PROFILE_VEC_PATH, {"version": profile["version"], "doc": doc, "titles": titles})
    return doc, titles


def _keyword_score(profile, job):
    """Fallback scorer: overlap between profile terms and the posting text."""
    terms = {norm_key(s["name"]) for s in profile.get("skills", [])}
    terms |= {w for t in profile.get("target_titles", []) for w in norm_key(t).split()}
    terms.discard("")
    text = norm_key(job["title"] + " " + job["description"])
    hits = sum(1 for t in terms if t and t in text)
    return hits / max(8, len(terms))


def rank_jobs(profile, jobs):
    """Attaches `prefilter` in [0,1] to every job and returns jobs sorted desc."""
    doc_vec, title_vecs = _profile_vectors(profile)

    if doc_vec is None:
        for j in jobs:
            j["prefilter"] = round(_keyword_score(profile, j), 4)
    else:
        cache = load_json(JOB_VEC_PATH, {})
        missing = [j for j in jobs if j["content_hash"] not in cache]
        if missing:
            texts = [f"{j['title']}. {j['company']}. {j['description'][:1200]}" for j in missing]
            for j, v in zip(missing, _embed(texts)):
                cache[j["content_hash"]] = v
            # Keep the cache bounded to hashes we still see
            live = {j["content_hash"] for j in jobs}
            cache = {h: v for h, v in cache.items() if h in live}
            save_json(JOB_VEC_PATH, cache)
        for j in jobs:
            v = cache[j["content_hash"]]
            best_title = max((_cos(v, t) for t in title_vecs), default=0.0)
            j["prefilter"] = round(0.65 * _cos(v, doc_vec) + 0.35 * best_title, 4)

    return sorted(jobs, key=lambda j: -j["prefilter"])

"""Shared configuration for the career agent pipeline.

All external credentials come from environment variables (GitHub Actions
secrets). Every keyed integration degrades gracefully when its secret is
absent, so the pipeline always completes with whatever sources are available.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# State lives on the `agent-data` branch, checked out to this directory by the
# workflow. Locally it defaults to ./agent-state (gitignored on main).
STATE_DIR = Path(os.environ.get("AGENT_STATE_DIR", str(REPO_ROOT / "agent-state")))

GITHUB_USER = "agentchieflou"
EMAIL_TO = "mbf.louard@gmail.com"

# Flash: fast, cheap, and free-tier eligible; one small call per run.
# If this name 404s (model retired/renamed), llm.py auto-discovers the best
# available flash model for the key and retries.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
# GOOGLE_API_KEY accepted as an alias — both names are common for Gemini keys.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")

# Add this block right below it to expose it to the llm engine:
if GEMINI_API_KEY:
    os.environ["LLM_GEMINI_KEY"] = GEMINI_API_KEY
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
USAJOBS_API_KEY = os.environ.get("USAJOBS_API_KEY", "")
USAJOBS_USER_AGENT = os.environ.get("USAJOBS_USER_AGENT", EMAIL_TO)
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", EMAIL_TO)
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

MAX_LLM_CANDIDATES = 15   # jobs sent to Gemini per run, after embedding prefilter
DESC_TRUNCATE = 800       # chars of description per job in the LLM prompt
TOP_N_DIGEST = 5
TOP_N_GRAPH_JOBS = 8
JOB_EXPIRY_DAYS = 14      # drop listings not seen at any source for this long

USER_AGENT = "career-agent/1.0 (+https://github.com/agentchieflou/agentchieflou.github.io)"

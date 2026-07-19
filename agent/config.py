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

# Haiku: cheapest current-generation model; one small call per run.
CLAUDE_MODEL = "claude-haiku-4-5"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
USAJOBS_API_KEY = os.environ.get("USAJOBS_API_KEY", "")
USAJOBS_USER_AGENT = os.environ.get("USAJOBS_USER_AGENT", EMAIL_TO)
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", EMAIL_TO)
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

MAX_LLM_CANDIDATES = 15   # jobs sent to Claude per run, after embedding prefilter
DESC_TRUNCATE = 800       # chars of description per job in the LLM prompt
TOP_N_DIGEST = 5
TOP_N_GRAPH_JOBS = 8
JOB_EXPIRY_DAYS = 14      # drop listings not seen at any source for this long

USER_AGENT = "career-agent/1.0 (+https://github.com/agentchieflou/agentchieflou.github.io)"

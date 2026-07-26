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

# Answers to the deterministic half of an application form, so the digest can
# pre-fill them instead of the owner retyping the same fields per posting.
# Everything here is already published on resume.html — nothing private, and
# agent-data is a public branch.
APPLICANT = {
    "first_name": "Michael",
    "last_name": "Louard",
    "email": EMAIL_TO,
    "phone": "216-904-9535",
    "location": "Raleigh, NC (open to remote, US)",
    "state": "North Carolina",   # spelled out, to match state dropdowns
    "linkedin": "https://linkedin.com/in/michael-louard",
    "github": "https://github.com/agentchieflou",
    "website": "https://agentchieflou.github.io",
}
# "How did you hear about us?" — answered the same way every time on purpose.
# It is the one free field on a standard form where the honest answer also
# demonstrates the skill being sold.
REFERRAL_ANSWER = ("Through an autonomous job-matching agent I built and run myself — "
                   "it reads employer ATS boards directly and surfaced this role. "
                   "Source: github.com/agentchieflou")

# Flash-Lite: cheapest available flash-tier model; one small call per run.
# If this name 404s (model retired/renamed), llm.py auto-discovers the best
# available flash model for the key and retries.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
# GOOGLE_API_KEY accepted as an alias — both names are common for Gemini keys.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")

# Expose it under the name the llm engine looks for.
if GEMINI_API_KEY:
    os.environ["LLM_GEMINI_KEY"] = GEMINI_API_KEY



def _env(*names, default=""):
    """First non-empty value among `names`, else `default`.

    Not the same as os.environ.get(name, default): GitHub Actions sets an env
    var to the EMPTY STRING when the secret behind it does not exist, so the
    key is present and a plain .get() returns "" instead of falling back. Any
    setting with a meaningful default has to be read this way.
    """
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


GITHUB_TOKEN = _env("GITHUB_TOKEN")
ADZUNA_APP_ID = _env("ADZUNA_APP_ID")
# Adzuna's own docs call it app_key; ADZUNA_API_KEY is accepted as an alias
# because that is an easy name to reach for when creating the secret.
ADZUNA_APP_KEY = _env("ADZUNA_APP_KEY", "ADZUNA_API_KEY")
USAJOBS_API_KEY = _env("USAJOBS_API_KEY")
# USAJobs requires the User-Agent to be the email the key is registered to.
USAJOBS_USER_AGENT = _env("USAJOBS_USER_AGENT", default=EMAIL_TO)
JOOBLE_API_KEY = _env("JOOBLE_API_KEY")
RAPIDAPI_KEY = _env("RAPIDAPI_KEY")
GMAIL_ADDRESS = _env("GMAIL_ADDRESS", default=EMAIL_TO)
GMAIL_APP_PASSWORD = _env("GMAIL_APP_PASSWORD")

MAX_LLM_CANDIDATES = 60  # jobs sent to Gemini per run, after embedding prefilter
DESC_TRUNCATE = 800       # chars of description per job in the LLM prompt
TOP_N_DIGEST = 15
TOP_N_GRAPH_JOBS = 50     # size of the displayed pool (skills.html graph/matrix/ledger)
MIN_NEW_PER_RUN = 20      # of TOP_N_GRAPH_JOBS, at least this many must be new vs. last run
JOB_EXPIRY_DAYS = 14      # drop listings not seen at any source for this long

# Hard floor. A posting must state compensation somewhere (structured ATS
# field, or extractable from its text) AND top out at or above this, or it is
# dropped before ranking. Employers that won't say what they pay don't make
# the list — the ATS sources below expose real comp fields, so this costs far
# less coverage than it did when everything came from aggregators.
MIN_SALARY_USD = 130_000

# Narrow exception to the stated-salary rule. Employers who post through
# their own ATS are reliable about the ROLE even when they withhold pay, and
# dropping all of them was the single largest loss in the funnel. So a capped
# number may through — but salary is then the only thing unknown about the
# posting, so nothing else is allowed to be:
#
#   employer-direct   an aggregator's copy does not count; it has to come
#                     from the company's own ATS
#   freshly posted    an old listing with no pay data is exactly where
#                     "employer-direct" stops being reassuring
#   clearly reachable the LLM has to place it in band, and score it well
#                     above the bar a salaried posting has to clear
#
# Anything failing one of these is still dropped outright.
NO_SALARY_SOURCES = {"greenhouse", "lever", "ashby", "smartrecruiters"}
NO_SALARY_MAX_AGE_DAYS = 14
NO_SALARY_MAX_IN_DIGEST = 5
NO_SALARY_MIN_SCORE = 80
NO_SALARY_MIN_CONFIDENCE = 0.7
# Seniority verdicts that count as a genuine step up rather than a lateral
# move or a long shot.
NO_SALARY_SENIORITY_FITS = ("target", "stretch")

# A job must share at least this many skills with the profile (per the LLM's
# matched_skills) to count as a real match anywhere downstream (digest or
# skills.html) — keeps one-skill-overlap noise out of "worth applying to".
MIN_SKILL_MATCHES = 3

# Rocchio-style negative feedback weight: how hard a rejected-job negative
# centroid pulls down the prefilter score of structurally similar postings.
# Kept well under 1 so rejections can suppress but never fully zero a score.
NEG_FEEDBACK_WEIGHT = 0.25

# ---------------- ATS job-board sources ----------------
# Greenhouse, Lever, Ashby and SmartRecruiters all publish a company's open
# roles as structured JSON at a stable, documented, keyless endpoint. That is
# the whole reason to prefer them: real employment-type, remote and
# compensation fields instead of a scraped blob, straight from the employer.
#
# They are per-company, not global, so agent/boards.json holds a registry of
# board slugs and agent/ats.py grows it automatically from ATS links seen in
# the aggregator sources. Boards are polled on a rotation so a large registry
# never turns into a large number of requests in any single run.
#
# Workable is deliberately absent: its keyless widget endpoint returns empty
# job lists and its SPI endpoint is 401-gated, so there is nothing to consume
# without a partner key.
BOARDS_PATH = REPO_ROOT / "agent" / "boards.json"
# Comfortably above the seeded registry, so every board is polled every run
# and the rotation only starts spreading load once discovery has grown the
# registry well past the seed. Daily polling of a few hundred boards is
# nothing to any of these APIs.
ATS_BOARDS_PER_RUN = 400
ATS_FETCH_WORKERS = 8        # concurrent board fetches
ATS_MAX_JOBS_PER_BOARD = 400  # guard against a pathological board dump

# ---------------- relevance gates (agent/role_filter.py) ----------------
# The owner's target disciplines. A posting whose title matches none of these
# is dropped before it costs anything: ATS boards return every open role at a
# company, and the aggregators are only as good as their query.
ROLE_FAMILIES = {
    "Business & Data Analytics": [
        r"\bbusiness analyst\b", r"\bbusiness analytics\b", r"\bdata analyst\b",
        r"\bproduct analyst\b", r"\banalytics (?:analyst|manager|lead|consultant)\b",
        r"\bbusiness intelligence\b", r"\bBI (?:analyst|developer|engineer|lead)\b",
        r"\breporting analyst\b", r"\binsights? (?:analyst|manager|lead)\b",
        r"\bdecision scien(?:ce|tist)\b", r"\bmarketing analytics\b",
    ],
    "Analytics & Data Engineering": [
        r"\banalytics engineer\b", r"\bdata engineer\b", r"\bdata platform\b",
        r"\banalytics (?:platform|infrastructure)\b", r"\bdata model(?:er|ing)\b",
        r"\bdata (?:warehouse|architect)\b", r"\bdata scientist\b",
    ],
    "Process Optimization & Business Operations": [
        r"\bprocess (?:optimi[sz]ation|improvement|engineer|analyst|excellence|manager)\b",
        r"\bbusiness process\b", r"\b(?:business|revenue|sales|data|technical) operations\b",
        r"\boperations (?:analyst|manager|engineer|lead)\b", r"\bbiz ?ops\b",
        r"\brev ?ops\b", r"\bautomation (?:engineer|analyst|lead|specialist)\b",
        r"\bcontinuous improvement\b", r"\bsix sigma\b", r"\bworkflow (?:engineer|analyst)\b",
    ],
    "Technical Strategy & Product": [
        r"\b(?:business|corporate|technical|technology|data|product|growth) strategy\b",
        r"\bstrategy (?:&|and) operations\b",
        r"\bstrateg(?:y|ic) (?:analyst|manager|lead|associate)\b",
        r"\btechnical program manager\b",
        r"\bprogram manager,? (?:data|analytics|ai)\b",
        r"\bproduct manager,? (?:data|analytics|ai|ml|platform)\b",
        r"\b(?:data|analytics|ai|ml) product manager\b",
    ],
    "AI & Machine Learning": [
        r"\bAI\b[^,]*\b(?:engineer|analyst|strategist|specialist|consultant|architect|lead|manager|developer|scientist)\b",
        r"\bapplied (?:AI|ML|machine learning|scientist)\b", r"\bmachine learning\b",
        r"\bML (?:engineer|ops|platform|scientist)\b", r"\bMLOps\b", r"\bLLM\b",
        r"\bgenerative ai\b", r"\bagentic\b", r"\bprompt engineer\b",
        r"\bforward[- ]deployed\b",
    ],
    "Quantitative & Risk": [
        r"\bquantitative\b", r"\bquant (?:analyst|developer|researcher)\b",
        r"\bmodel (?:risk|development|validation)\b",
        r"\brisk (?:analyst|modeler|analytics)\b", r"\bcredit risk\b",
        r"\bpricing (?:analyst|strategy|manager)\b",
    ],
    "Solutions & Technical Consulting": [
        r"\bsolutions? (?:engineer|architect|consultant|analyst|manager)\b",
        r"\b(?:technical|analytics|data|management) consultant\b",
        r"\bimplementation (?:consultant|engineer|manager)\b",
        r"\bsystems? (?:analyst|integration)\b",
    ],
}

# Checked before the families above and wins over them — these disciplines are
# never a fit regardless of how the title reads.
TITLE_EXCLUDE_PATTERNS = [
    r"\baccount executive\b", r"\bsales (?:representative|rep|manager|director|executive|associate|engineer|lead)\b",
    r"\b(?:SDR|BDR)\b", r"\bbusiness development\b",
    r"\brecruit(?:er|ing|ment)\b", r"\btalent acquisition\b", r"\bpeople (?:operations|partner)\b",
    r"\bhuman resources\b",
    r"\b(?:brand|content|field|product|growth|performance) marketing\b",
    r"\bmarketing (?:manager|director|specialist|coordinator|associate)\b",
    r"\bdesigner\b", r"\b(?:ux|ui|visual|graphic) design\b",
    r"\bnurse\b", r"\bclinical\b", r"\bphysician\b", r"\bpharmac", r"\bveterinar",
    r"\bcounsel\b", r"\blegal\b", r"\bparalegal\b",
    r"\baccountant\b", r"\baccounting\b", r"\bcontroller\b", r"\bpayroll\b",
    r"\bbookkeep", r"\baudit(?:or)?\b",
    r"\b(?:front[- ]?end|back[- ]?end|full[- ]?stack)\b",
    r"\b(?:ios|android|mobile|game|graphics|firmware|embedded|hardware)\b",
    r"\bsite reliability\b", r"\bSRE\b", r"\bdevops\b", r"\bsecurity engineer\b",
    r"\bnetwork engineer\b", r"\bquality assurance\b", r"\btest engineer\b", r"\bSDET\b",
    r"\bcustomer (?:support|success|experience)\b", r"\btechnical support\b",
    r"\bhelp ?desk\b", r"\bsupport (?:engineer|specialist|agent)\b",
    r"\bwarehouse\b", r"\bdriver\b", r"\bteacher\b", r"\btutor\b",
    r"\b(?:copy)?writer\b", r"\beditor\b", r"\bjournalist\b",
    r"\bexecutive assistant\b", r"\boffice manager\b", r"\bfacilities\b",
    r"\bteller\b", r"\bbarista\b",
]

# Seniority band. The owner is ~4 years into a quantitative/analytics career
# and looking for a genuine step up — not a Principal/Staff/Director role that
# will never call back, and not a step down into an entry-level seat. Titles
# matching these are dropped outright; "Senior", "Lead" and "Manager" stay in
# because those ARE the step up.
SENIORITY_EXCLUDE_PATTERNS = [
    r"\bprincipal\b", r"\bstaff\b", r"\bdistinguished\b", r"\bfellow\b",
    r"\bdirector\b", r"\bvice president\b", r"\bVP\b", r"\bSVP\b", r"\bEVP\b",
    r"\bhead of\b", r"\bchief\b", r"\bC[TFEOI]O\b",
    r"\bsenior manager\b", r"\bsr\.? manager\b", r"\bgroup manager\b",
    r"\benterprise architect\b", r"\bchief architect\b",
    r"\bintern(?:ship)?\b", r"\bapprentice\b", r"\bjunior\b", r"\bjr\.?\b",
    r"\bentry[- ]level\b", r"\bnew grad\b", r"\btrainee\b",
]

# Full-time only.
EMPLOYMENT_EXCLUDE_PATTERNS = [
    r"\bcontract(?:or)?\b", r"\bpart[- ]time\b", r"\bintern(?:ship)?\b",
    r"\btemporary\b", r"\bfreelance\b", r"\bseasonal\b", r"\bfixed[- ]term\b",
    r"\b1099\b", r"\bC2C\b", r"\bcorp[- ]to[- ]corp\b",
]

# Queries sent to the keyword-search sources (Adzuna, USAJobs, Jooble,
# JSearch). Deliberately NOT the profile's target_titles: the LLM produces
# aspirational positioning like "Senior Business Analytics Engineer" or
# "Process Automation Lead", which describe the owner well but match almost
# nothing in a job index — searching them returned zero results from every
# keyed source. These are the canonical, high-volume titles the same roles
# are actually posted under. target_titles still drives embedding similarity,
# where the aspirational phrasing is an asset.
SEARCH_QUERIES = [
    "Business Analyst",
    "Data Analyst",
    "Analytics Engineer",
    "Business Intelligence Analyst",
    "Business Operations Analyst",
    "Process Improvement Analyst",
    "Data Scientist",
    "AI Engineer",
    "Technical Program Manager",
    "Solutions Consultant",
]

# First professional year on the resume; candidate_years() derives experience
# from it so the seniority maths never goes stale.
CAREER_START_YEAR = 2022
# A posting asking for more than (candidate years + this) is out of band.
MAX_YEARS_REQUIRED_OVER_CANDIDATE = 2

# Legacy skills the owner no longer wants surfaced anywhere — filtered out of
# the extracted profile so they never reach ranking, scoring, or the graph.
# Matched case-insensitively as whole words against skill names.
SKILL_BLOCKLIST_PATTERNS = [r"\bSAS\b", r"\bStata\b", r"\bBERT\b", r"\bNLP\b",
                            r"\bCOBOL\b", r"\bGo\b"]

# Company accessibility gate (agent/company_enrich.py): "we want to work for
# companies that make themselves accessible" — a company must have a
# resolvable domain AND a real, fetchable self-description (homepage, or an
# "about us" blurb already present in one of its own postings). Companies
# that fail this are excluded entirely, not just deprioritized. Purely
# mechanical (no LLM) — sector labeling is a separate, LLM-driven step that
# rides along in the existing scoring call rather than adding a second one.
COMPANY_ENRICH_MAX_PER_RUN = 20   # new companies resolved per run (politeness cap)
COMPANY_DESC_MIN_USEFUL = 200     # extracted chars below this = "no real description"
COMPANY_RECHECK_DAYS = 90         # re-attempt previously-inaccessible companies after this long

SECTORS = [
    "Fintech", "Healthcare", "Enterprise SaaS/DevTools", "AI/ML Infra",
    "Consumer/E-commerce", "Government/Public Sector", "Media/Gaming",
    "Consulting", "Other",
]

USER_AGENT = "career-agent/1.0 (+https://github.com/agentchieflou/agentchieflou.github.io)"

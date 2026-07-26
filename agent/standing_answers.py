"""Answers that are the same on every application form.

Measured across eight real Greenhouse forms: 52 questions needed answering,
36 distinct by exact wording — but that count is inflated by phrasing. One
question appears in five costumes:

    "will you require sponsorship for employment visa status now or in the future"
    "do you require immigration sponsorship to work for {company} in the United States"
    "will you now or at any time in the future require visa sponsorship"
    "this role does not offer visa sponsorship, are you currently authorized to work..."
    "do you now or will you in the future require sponsorship"

Group by meaning and roughly eight in ten questions are standing facts about
the owner — authorization, sponsorship, age, prior employment, non-compete,
government-official screeners. Those never need answering twice. What is left
is two to four genuinely per-job questions, which is where the owner's
attention is actually worth spending.

Two things this module refuses to answer, deliberately:

  consent and arbitration  "Please confirm receipt of the global data privacy
                           notice and US arbitration agreement" appeared on
                           three of eight forms. That is accepting a legal
                           agreement and belongs to a human.
  self-identification      EEO, demographics, pronouns, and "how do you use
                           AI tools" are the owner's to characterise.

Answers resolve against the field's actual shape. A select only accepts its
own options, so each entry carries a pattern for picking one; where no option
matches — a "How did you hear about us?" dropdown offering only Career Page /
LinkedIn / Indeed, for instance — the question is handed back rather than
answered wrongly.
"""
import re

from config import APPLICANT, REFERRAL_ANSWER

# Never auto-answer these, whatever else matches.
NEVER_ANSWER = re.compile(
    r"arbitration|privacy notice|confirm receipt|terms? (?:and|&) conditions|"
    r"consent to|acknowledge (?:that|the)|"
    r"gender|race|ethnicit|veteran|disabilit|pronoun|sexual orientation|"
    r"how you use ai tools|describes how you use", re.I)

# Select options are rarely the literal words "Yes" and "No". Affirm phrases
# the same answer as "I have not previously been employed at Affirm", so
# matching on "no" alone silently left those questions for a human.
# The lookahead keeps "I am not a relative of a government official" — a NO
# dressed as a sentence — out of the YES matcher.
_YES = re.compile(r"^\s*(?:yes\b|i\s+(?:am|do|have|was|can)\b(?!\s+not))", re.I)
_NO = re.compile(r"^\s*(?:no\b|none\b|never\b|not\b|"
                 r"i\s+(?:am|do|have|did|was)\s+not\b|"
                 r"i\s+(?:haven'?t|don'?t|didn'?t|wasn'?t|am\s+not)\b)", re.I)

# Order matters. "This role does not offer visa sponsorship — are you
# currently authorized to work in the US?" mentions sponsorship but is asking
# about authorization, so the authorization rule has to win. It does, because
# the sponsorship rule additionally requires a "will you require" stem.
STANDING = [
    {
        "id": "visa_sponsorship",
        "match": re.compile(r"\b(?:require|need|request)\b[^?]{0,60}"
                            r"\b(?:sponsorship|visa|work authoriz)", re.I),
        "select": _NO,
        "text": "No. I am a U.S. citizen and do not now, nor will I in the future, "
                "require sponsorship for employment visa status.",
    },
    {
        "id": "work_authorization",
        "match": re.compile(r"\bauthorized to work\b|\bwork authorization\b|"
                            r"\blegally (?:authorized|eligible) to\b", re.I),
        "select": _YES,
        "text": "Yes — authorized to work in the United States for any employer.",
    },
    {
        "id": "age_18",
        "match": re.compile(r"\b(?:at least|18 or older|over the age of)\b[^?]{0,20}\b18\b|"
                            r"\b18\b[^?]{0,20}\b(?:years of age|or older)\b", re.I),
        "select": _YES,
        "text": "Yes.",
    },
    {
        "id": "prior_employment",
        "match": re.compile(r"previously\s+(?:been\s+)?(?:employed|worked)|"
                            r"\bformer(?:ly)? (?:an? )?employee\b|"
                            r"ever (?:been employed|worked) (?:by|at|for)", re.I),
        "select": _NO,
        "text": "No.",
    },
    {
        "id": "relatives_at_company",
        "match": re.compile(r"(?:family members?|relatives?)[^?]{0,40}"
                            r"(?:employed|work)", re.I),
        "select": _NO,
        "text": "No.",
    },
    {
        "id": "non_compete",
        "match": re.compile(r"non-?compete|restrictive covenant", re.I),
        "select": _NO,
        "text": "No.",
    },
    {
        "id": "government_official_relative",
        "match": re.compile(r"(?:relative|family member)[^?]{0,40}government official", re.I),
        "select": _NO,
        "text": "No.",
    },
    {
        "id": "government_official",
        "match": re.compile(r"government official|politically exposed", re.I),
        "select": _NO,
        "text": "No.",
    },
    {
        "id": "referral_person",
        "match": re.compile(r"were you referred|referred (?:to this|by a)", re.I),
        "select": _NO,
        "text": "No.",
    },
    {
        "id": "how_heard",
        "match": re.compile(r"how did you (?:first\s+)?(?:hear|find out|learn|come)\s+"
                            r"(?:about|across)|referr?al source|"
                            r"where did you (?:hear|find)", re.I),
        # These are usually dropdowns of fixed sources (Career Page /
        # LinkedIn / Indeed) and none of them is true, so only "Other" is
        # selectable — picking a near-miss would be a lie on an application.
        # Where the field is free text, the full answer goes in.
        "select": re.compile(r"^other\b", re.I),
        "text": REFERRAL_ANSWER,
    },
    {
        "id": "linkedin",
        "match": re.compile(r"linked\s*-?in", re.I),
        "select": None,
        "text": APPLICANT["linkedin"],
    },
    {
        "id": "github",
        "match": re.compile(r"git\s*hub", re.I),
        "select": None,
        "text": APPLICANT["github"],
    },
    {
        "id": "portfolio",
        "match": re.compile(r"portfolio|personal (?:web)?site|\bwebsite\b", re.I),
        "select": None,
        "text": APPLICANT["website"],
    },
    {
        "id": "location",
        "match": re.compile(r"what location|current (?:city|location)|"
                            r"where (?:are you|do you) (?:based|located|live|reside)|"
                            r"which .{0,24}(?:state|province)|what city|"
                            r"from where do you intend to work", re.I),
        # State dropdowns are answerable exactly; free-text takes the full line.
        "select": re.compile(r"^north carolina\b", re.I),
        "text": APPLICANT["location"],
    },
]

# Straight field-name matches for the fields every form opens with.
BY_FIELD_NAME = {
    "first_name": APPLICANT["first_name"],
    "last_name": APPLICANT["last_name"],
    "email": APPLICANT["email"],
    "phone": APPLICANT["phone"],
}
_PREFERRED_NAME = re.compile(r"^preferred (?:first )?name", re.I)


def _options(question):
    out = []
    for field in question.get("fields") or []:
        for value in field.get("values") or []:
            label = value.get("label")
            if label:
                out.append(label)
    return out


def _is_select(question):
    return any((f.get("type") or "").startswith("multi_value")
               for f in (question.get("fields") or []))


def resolve(question):
    """The settled answer for this question, or None if it needs a human.

    Returns (answer, rule_id). `answer` is guaranteed to be one of the
    field's own options when the field is a select.
    """
    label = (question.get("label") or "").strip()
    if not label or NEVER_ANSWER.search(label):
        return None, None

    fields = question.get("fields") or []
    name = (fields[0].get("name") if fields else "") or ""
    for key, value in BY_FIELD_NAME.items():
        if name == key:
            return value, key
    if _PREFERRED_NAME.search(label):
        return APPLICANT["first_name"], "preferred_name"

    for rule in STANDING:
        if not rule["match"].search(label):
            continue
        if _is_select(question):
            if rule["select"] is None:
                return None, None  # no honest option to pick
            for option in _options(question):
                if rule["select"].search(option):
                    return option, rule["id"]
            return None, None      # nothing matched; leave it alone
        return rule["text"], rule["id"]
    return None, None


def summary():
    """The standing answers themselves, for the digest's one-time reference."""
    rows = [(k.replace("_", " ").title(), v) for k, v in BY_FIELD_NAME.items()]
    rows.append(("Location", APPLICANT["location"]))
    rows.append(("LinkedIn", APPLICANT["linkedin"]))
    rows.append(("GitHub", APPLICANT["github"]))
    rows.append(("Work authorization", "Yes — authorized in the US for any employer"))
    rows.append(("Visa sponsorship", "No — not now or in the future"))
    rows.append(("18 or older", "Yes"))
    rows.append(("Non-compete", "No"))
    rows.append(("Government official / relative", "No"))
    rows.append(("Previously employed there", "No"))
    rows.append(("How did you hear about us", REFERRAL_ANSWER))
    return rows

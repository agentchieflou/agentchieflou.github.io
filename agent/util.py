"""Small shared helpers: JSON state files, hashing, HTML-to-text."""
import datetime as dt
import hashlib
import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path

log = logging.getLogger("career-agent")

VOID_TAGS = {"br", "img", "meta", "link", "input", "hr", "source", "wbr", "area", "base", "col", "embed", "track"}


def load_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


class _TextExtractor(HTMLParser):
    """Extracts visible text, skipping script/style and elements whose class
    list contains any of `skip_classes` (used to drop the resume's joke
    'kate-only' copy)."""

    def __init__(self, skip_classes=()):
        super().__init__(convert_charrefs=True)
        self.skip_classes = set(skip_classes)
        self.parts = []
        self._stack = []  # skip flags for open (non-void) tags

    def _should_skip(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            return True
        classes = (dict(attrs).get("class") or "").split()
        return bool(self.skip_classes.intersection(classes))

    def handle_starttag(self, tag, attrs):
        if tag in VOID_TAGS:
            return
        self._stack.append(self._should_skip(tag, attrs))

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if any(self._stack):
            return
        text = data.strip()
        if text:
            self.parts.append(text)


def html_to_text(html: str, skip_classes=()) -> str:
    parser = _TextExtractor(skip_classes)
    try:
        parser.feed(html)
    except Exception:
        # Fall back to a crude tag strip rather than failing the run
        return re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"[ \t]+", " ", "\n".join(parser.parts))


def norm_key(text: str) -> str:
    """Normalization used for dedupe keys (company/title)."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


# Seniority/level decorations that a re-post routinely gains or loses
# ("Data Analyst" -> "Data Analyst II"), stripped so they don't defeat a
# role_key match.
_ROLE_NOISE = re.compile(
    r"\b(?:i{1,3}|iv|v|vi{0,3}|[1-5]|senior|sr|junior|jr|lead|staff|principal|"
    r"associate|remote|us|usa|united states|full[ -]?time|contract|hybrid)\b", re.I)


def role_key(company: str, title: str) -> str:
    """Opaque, stable fingerprint for "this role at this company".

    Rejections are recorded against this rather than against the job id,
    because the id is a hash of the URL: the same role re-posted at a new URL,
    or surfaced by a second source, is a different id and would otherwise come
    straight back. Deliberately company+title, never company alone — the point
    is to stop one role reappearing, not to blacklist an employer.

    Hashed because it is written to a public branch.
    """
    c = norm_key(company)
    t = re.sub(r"\s+", " ", _ROLE_NOISE.sub(" ", norm_key(title))).strip()
    if not c or not t:
        return ""
    return sha1(f"{c}|{t}")


def parse_timestamp(value):
    """Best-effort UTC datetime from the assorted shapes sources emit, or None.

    Handles ISO 8601 with a Z, with an offset, or naive, plus epoch seconds
    and epoch milliseconds — Lever returns createdAt in milliseconds, which
    plain ISO parsing would silently reject.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        n = float(value)
        if n > 1e11:  # milliseconds, not seconds
            n /= 1000.0
        try:
            return dt.datetime.fromtimestamp(n, dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        m = re.match(r"\d{4}-\d{2}-\d{2}", text)
        if not m:
            return None
        try:
            parsed = dt.datetime.fromisoformat(m.group(0))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def posted_within_days(value, days):
    """True only when the timestamp parses AND is that recent.

    Fails closed: an unparseable or absent date is treated as too old. This
    gates postings that have no stated salary, so "we could not tell how old
    this is" has to mean no.
    """
    parsed = parse_timestamp(value)
    if parsed is None:
        return False
    age = dt.datetime.now(dt.timezone.utc) - parsed
    # A small negative age is clock skew between us and the source, not a
    # posting from the future.
    return dt.timedelta(days=-2) <= age <= dt.timedelta(days=days)


_SALARY_NUM = re.compile(r"(\d{1,3}(?:[,.]\d{3})+|\d+(?:\.\d+)?)\s*([kK])?")


def salary_max_usd(salary_text):
    """Best-effort yearly max USD from a freeform salary string, else None.

    Handles "$120,000 - $150,000", "$60k-$80k", "$45/hr", "130000". Returns
    None when nothing parseable — unknown salary is not the same as low
    salary, and callers must treat it that way.
    """
    if not salary_text:
        return None
    text = re.sub(r"\b40[13]\s*\(?[kb]\)?\b", "", str(salary_text), flags=re.I)
    vals = []
    for num, k in _SALARY_NUM.findall(text):
        v = float(num.replace(",", ""))
        if k:
            v *= 1000
        vals.append(v)
    vals = [v for v in vals if v >= 10]  # drop stray small numbers ("401k" is caught by k*1000)
    if not vals:
        return None
    mx = max(vals)
    if re.search(r"/\s*(hr|hour)|hourly|per hour", text, re.I) or mx < 1000:
        mx *= 2080  # hourly -> yearly
    elif re.search(r"/\s*(mo|month)|monthly|per month", text, re.I):
        mx *= 12
    elif mx < 10_000:  # bare "130"-style shorthand, assume thousands
        mx *= 1000
    return int(mx)


US_REMOTE_HINTS = re.compile(
    r"\b(usa|u\.s\.|us|united states|americas|north america|worldwide|anywhere|global)\b", re.I)


def us_friendly(location_text):
    """True when a location string is blank or reads as US-inclusive.

    Blank counts as friendly: several sources omit location on remote roles,
    and a later gate (or the LLM) will catch a genuinely foreign posting.
    """
    return not location_text or bool(US_REMOTE_HINTS.search(location_text))


_REMOTE_DISQUALIFIERS = re.compile(
    r"\bhybrid\b|\bon-?site\b|\bin.office\b|\bin-person\b|"
    r"\d+\s*(?:-\s*\d+\s*)?days?\s*(?:a|per)\s*week\s*(?:in|on-?site|in.office)|"
    r"\brelocat(?:e|ion|ing)\b|\bmust (?:be|reside|live) (?:in|near|within)\b",
    re.I)


def looks_genuinely_remote(text):
    """True only when text reads as fully remote with no hybrid/onsite tell.

    A bare "remote" substring match is too weak — plenty of hybrid/onsite
    postings mention "remote" once (a stipend, an occasional day) without
    being remote roles. This additionally rejects the common disqualifying
    phrasing (hybrid, on-site, N days a week in office, relocation required).
    """
    text = text or ""
    if "remote" not in text.lower():
        return False
    return not _REMOTE_DISQUALIFIERS.search(text)


_SALARY_SNIPPET = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?\s?[kK]?"
    r"(?:\s?(?:-|–|—|to)\s?\$?\s?\d[\d,]*(?:\.\d+)?\s?[kK]?)?"
    r"(?:\s?/\s?(?:hr|hour|yr|year|mo|month)|\s?per\s?(?:hour|year|month))?")


def find_salary_snippet(text):
    """Pulls a stated salary out of freeform posting text (HN comments etc.).

    Returns the best matching snippet or None. Deliberately conservative:
    bare dollar amounts under $1,000 count only as plausible hourly wages
    ($15-$200), and anything outside $40k-$1.2M/yr is treated as noise
    (bonuses, revenue figures) rather than compensation.
    """
    best, best_y = None, 0
    for m in _SALARY_SNIPPET.finditer((text or "")[:4000]):
        s = m.group(0)
        raw = [float(n.replace(",", "")) for n, _ in _SALARY_NUM.findall(s)]
        if not raw:
            continue
        if max(raw) < 1000 and "k" not in s.lower() and not re.search(r"/|per", s):
            if not (15 <= max(raw) <= 200):
                continue
        y = salary_max_usd(s)
        if y and 40_000 <= y <= 1_200_000 and y > best_y:
            best, best_y = s.strip(), y
    return best

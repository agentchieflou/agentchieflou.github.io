"""Small shared helpers: JSON state files, hashing, HTML-to-text."""
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

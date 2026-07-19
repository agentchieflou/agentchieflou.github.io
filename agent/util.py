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

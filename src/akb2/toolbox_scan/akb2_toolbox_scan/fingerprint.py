"""Read-only fingerprint + safe header sniffing.

We only ever:
  * stat() to read size and mtime
  * open() in binary read mode and hash the bytes
  * read up to HEADER_BYTES of safe text-decoded header to detect a
    YAML frontmatter `name`/`description` and AKB backlinks

We never execute, never import, never subprocess any candidate file.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path

HEADER_BYTES = 4096
SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password|bearer)\s*[:=]"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
)
AKB_BACKLINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass(frozen=True)
class FingerprintResult:
    sha256: str | None
    size_bytes: int | None
    mtime: str | None
    error: str | None = None


@dataclass
class HeaderSniff:
    name: str | None = None
    description: str | None = None
    has_shebang: bool = False
    has_akb_backlink: bool = False
    backlink_targets: list[str] = field(default_factory=list)
    looks_like_secret: bool = False


def fingerprint(path: Path) -> FingerprintResult:
    try:
        st = path.stat()
        size = st.st_size
        mtime = _iso_mtime(st.st_mtime)
        sha = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                sha.update(chunk)
        return FingerprintResult(sha256=sha.hexdigest()[:16], size_bytes=size, mtime=mtime)
    except (OSError, PermissionError) as exc:
        return FingerprintResult(sha256=None, size_bytes=None, mtime=None, error=str(exc))


def sniff_header(path: Path) -> HeaderSniff:
    sniff = HeaderSniff()
    try:
        with path.open("rb") as fh:
            blob = fh.read(HEADER_BYTES)
    except (OSError, PermissionError):
        return sniff

    try:
        text = blob.decode("utf-8", errors="replace")
    except Exception:
        return sniff

    if text.startswith("#!"):
        sniff.has_shebang = True

    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end]
            for line in fm.splitlines():
                m = re.match(r"^(name|description)\s*:\s*(.+?)\s*$", line)
                if not m:
                    continue
                key, value = m.group(1), m.group(2).strip().strip('"').strip("'")
                if key == "name" and not sniff.name:
                    sniff.name = value
                elif key == "description" and not sniff.description:
                    sniff.description = value[:200]

    targets = AKB_BACKLINK_PATTERN.findall(text)
    if targets:
        sniff.has_akb_backlink = True
        sniff.backlink_targets = [t.split("|", 1)[0] for t in targets[:5]]

    if any(p.search(text) for p in SECRET_PATTERNS):
        sniff.looks_like_secret = True

    return sniff


def _iso_mtime(seconds: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

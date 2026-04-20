"""Input source resolution: local paths and GitHub URLs.

Converts user-provided inputs into local file paths that the parser
can consume. GitHub files are streamed to a temp file to avoid loading
the full GDS into memory.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

# Match GitHub blob URLs and convert to raw
# e.g. https://github.com/owner/repo/blob/branch/path/to/file.gds
_GITHUB_BLOB_RE = re.compile(
    r"^https?://github\.com/"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+)"
    r"/blob/(?P<ref>[^/]+)/(?P<path>.+)$"
)

# Already a raw URL
_GITHUB_RAW_RE = re.compile(
    r"^https?://raw\.githubusercontent\.com/"
)

# Timeout for downloads (connect, read) in seconds
_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 300.0  # 5 min for large files


class SourceError(Exception):
    """Raised when the input source cannot be resolved."""


def resolve_source(source: str) -> tuple[Path, bool]:
    """Resolve a source string to a local file path.

    Args:
        source: Local file path or GitHub URL.

    Returns:
        (path, is_temp) - path to the file, and whether it's a temp
        file that the caller should clean up.

    Raises:
        SourceError: If the source cannot be resolved.
    """
    # Local path
    if not source.startswith(("http://", "https://")):
        p = Path(source).expanduser().resolve()
        if not p.is_file():
            raise SourceError(f"File not found: {p}")
        return p, False

    # GitHub blob URL -> raw URL
    raw_url = _to_raw_url(source)
    return _download_to_temp(raw_url), True


def _to_raw_url(url: str) -> str:
    """Convert a GitHub URL to its raw content URL."""
    if _GITHUB_RAW_RE.match(url):
        return url

    m = _GITHUB_BLOB_RE.match(url)
    if m:
        return (
            f"https://raw.githubusercontent.com/"
            f"{m.group('owner')}/{m.group('repo')}/"
            f"{m.group('ref')}/{m.group('path')}"
        )

    # Generic URL - try as-is
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        return url

    raise SourceError(f"Cannot resolve URL: {url}")


def _download_to_temp(url: str) -> Path:
    """Stream-download a URL to a temporary file.

    Uses streaming to avoid loading the full file into memory,
    critical for multi-GB GDS downloads.
    """
    try:
        with httpx.Client(
            timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
            follow_redirects=True,
        ) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()

                suffix = ".gds"
                tmp = tempfile.NamedTemporaryFile(
                    suffix=suffix, prefix="gds_meta_", delete=False
                )
                try:
                    for chunk in resp.iter_bytes(chunk_size=1 << 20):  # 1 MB
                        tmp.write(chunk)
                    tmp.close()
                    return Path(tmp.name)
                except Exception:
                    tmp.close()
                    os.unlink(tmp.name)
                    raise

    except httpx.HTTPStatusError as e:
        raise SourceError(f"HTTP {e.response.status_code} downloading {url}") from e
    except httpx.RequestError as e:
        raise SourceError(f"Download failed for {url}: {e}") from e

"""Shared document fetching and parsing utilities for RAG ingestion."""

import io
import re
from pathlib import Path

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".json"}

_GDOCS_RE = re.compile(r"https://docs\.google\.com/document/d/([^/?#]+)")
_GSHEETS_RE = re.compile(r"https://docs\.google\.com/spreadsheets/d/([^/?#]+)")


def fetch_url(url: str) -> str | None:
    """Fetch text content from a URL (Google Docs/Sheets or general web page)."""
    import httpx

    m = _GDOCS_RE.match(url)
    if m:
        export_url = f"https://docs.google.com/document/d/{m.group(1)}/export?format=txt"
        try:
            resp = httpx.get(export_url, follow_redirects=True, timeout=30)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Google Docs fetch failed ({e.response.status_code}). "
                "ドキュメントが「リンクを知っている全員」に公開されているか確認してください。"
            ) from e

    m = _GSHEETS_RE.match(url)
    if m:
        export_url = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
        try:
            resp = httpx.get(export_url, follow_redirects=True, timeout=30)
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Google Sheets fetch failed ({e.response.status_code}). "
                "スプレッドシートが「リンクを知っている全員」に公開されているか確認してください。"
            ) from e

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise RuntimeError("beautifulsoup4 not installed — run: pip install beautifulsoup4")

    import httpx

    try:
        resp = httpx.get(url, follow_redirects=True, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"URL fetch failed ({e.response.status_code}): {url}") from e

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def read_file(path: Path) -> str | None:
    """Read a file from disk and return its text content."""
    return _parse(path.suffix.lower(), path.read_bytes(), path.name)


def read_bytes(data: bytes, filename: str) -> str | None:
    """Parse document bytes (e.g. from a Discord attachment download)."""
    suffix = Path(filename).suffix.lower()
    return _parse(suffix, data, filename)


def _parse(suffix: str, data: bytes, name: str) -> str | None:
    if suffix in (".txt", ".md"):
        return data.decode("utf-8", errors="replace")

    if suffix == ".pdf":
        try:
            from pdfminer.high_level import extract_text as pdf_extract
        except ImportError:
            raise RuntimeError("pdfminer.six not installed — run: pip install pdfminer.six")
        return pdf_extract(io.BytesIO(data))

    if suffix == ".json":
        import json
        obj = json.loads(data.decode("utf-8"))
        if isinstance(obj, list):
            return "\n".join(str(item) for item in obj)
        if isinstance(obj, dict):
            return "\n".join(f"{k}: {v}" for k, v in obj.items())
        return str(obj)

    raise RuntimeError(f"Unsupported file format: {suffix}. 対応形式: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")

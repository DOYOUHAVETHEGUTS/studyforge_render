"""
Ingestion / text extraction.

Supports: PDF (pypdf), DOCX (python-docx), TXT/MD (plain), and URLs (requests + a
lightweight HTML-to-text). Includes a scanned-PDF gate: if a PDF yields almost no
extractable text, we flag it rather than silently summarizing empty pages (a V1
silent-failure mode).
"""
import re
from pathlib import Path


class ExtractionError(Exception):
    pass


class ScannedPdfError(ExtractionError):
    """Raised when a PDF appears to be scanned (little/no extractable text)."""


def extract_pdf_sample(pdf_path, max_pages=8):
    """First N pages only — used for material-type inference and a quick
    scanned-PDF check without paying for a full-document extraction."""
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    n = min(max_pages, len(reader.pages))
    return "\n".join((reader.pages[i].extract_text() or "") for i in range(n))


def extract_pdf_pages(pdf_path):
    """Return list of per-page text strings (1-based order)."""
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    pages = [(p.extract_text() or "") for p in reader.pages]
    total_chars = sum(len(t.strip()) for t in pages)
    if reader.pages and total_chars < 100 * len(reader.pages) * 0.1:
        # heuristic: <~10 chars/page average => almost certainly scanned
        if total_chars < max(200, len(reader.pages) * 5):
            raise ScannedPdfError(
                f"PDF yielded only {total_chars} chars over {len(reader.pages)} pages; "
                "likely scanned. Run OCR (e.g. ocrmypdf) first, then re-add."
            )
    return pages


def extract_pdf_range(pdf_path, start_page, end_page):
    """1-based inclusive page range -> text."""
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    start = max(1, start_page) - 1
    end = min(len(reader.pages), end_page)
    return "\n".join((reader.pages[i].extract_text() or "") for i in range(start, end))


def extract_docx(path):
    import docx
    d = docx.Document(str(path))
    return "\n".join(p.text for p in d.paragraphs)


def extract_txt(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def extract_url(url):
    import requests
    resp = requests.get(url, timeout=30, headers={"User-Agent": "StudyForge/2.0"})
    resp.raise_for_status()
    html = resp.text
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def extract_file(path):
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return "\n".join(extract_pdf_pages(path))
    if ext == ".docx":
        return extract_docx(path)
    if ext in (".txt", ".md"):
        return extract_txt(path)
    raise ExtractionError(f"Unsupported file type: {ext}")

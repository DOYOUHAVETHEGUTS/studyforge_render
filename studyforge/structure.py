"""
Structure detection (deterministic first, LLM only as fallback).

Two jobs:
  1. Infer material_type (textbook | language | paper | reference) so the
     downstream processing strategy can differ.
  2. Split source into sections. For textbook PDFs: use bookmarks, but tolerate
     titles that don't literally start with "Chapter" (a V1 brittleness). For
     loose material: heading heuristic, then fixed-size chunks.

Agent 1 (LLM structure agent) is only invoked when heuristics are low-confidence;
see agents.structure_agent and pipeline.build_sections.
"""
import re

HEADING_RE = re.compile(
    r"^\s*(chapter|unit|lesson|section|part|module|lecci[oó]n|cap[ií]tulo)\b", re.I)

LANGUAGE_HINTS = re.compile(
    r"\b(vocabulary|grammar|conjugat|verb tense|lecci[oó]n|cap[ií]tulo|"
    r"gramática|vocabulario|translation|bilingual|pronunciation)\b", re.I)
PAPER_HINTS = re.compile(r"\b(abstract|related work|methodology|references|doi:|arxiv)\b", re.I)
NARRATIVE_HINTS = re.compile(
    r"\b(a novel|chapter one|prologue|epilogue|copyright[^\n]{0,40}(fiction|novel)|"
    r"mariner books|penguin books|a memoir)\b", re.I)
# exam/textbook signals: objectives, review questions, key terms, etc.
TEXTBOOK_HINTS = re.compile(
    r"\b(exam objective|review questions|key terms|learning objectives|chapter summary|"
    r"certification|study guide|answer key)\b", re.I)


def infer_material_type(name, text, declared=None):
    if declared in ("textbook", "language", "paper", "reference", "narrative"):
        return declared
    sample = f"{name}\n{text[:4000]}"
    if LANGUAGE_HINTS.search(sample):
        return "language"
    if PAPER_HINTS.search(sample):
        return "paper"
    if TEXTBOOK_HINTS.search(sample):
        return "textbook"
    if NARRATIVE_HINTS.search(sample):
        return "narrative"
    return "textbook"


def _looks_like_heading(line):
    s = line.strip()
    if not s or len(s) > 90:
        return None
    if HEADING_RE.match(s):
        return s
    # ALL-CAPS short line or "N. Title" style
    if len(s.split()) <= 10 and (s.isupper() or re.match(r"^\d+(\.\d+)*\s+\S", s)):
        return s
    return None


def detect_pdf_chapters(pdf_path):
    """Bookmark-based chapter ranges. Tolerant: keeps any top-level outline entry,
    not only ones starting with 'Chapter'."""
    from pypdf import PdfReader
    reader = PdfReader(str(pdf_path))
    entries = []
    try:
        outline = reader.outline
    except Exception:
        outline = []
    for item in outline:
        if isinstance(item, list):
            continue
        title = (getattr(item, "title", "") or "").strip()
        try:
            pg = reader.get_destination_page_number(item)
        except Exception:
            continue
        if title:
            entries.append((title, pg))
    if not entries:
        return []
    # keep entries that look like real content divisions; if too few, keep all
    chaptery = [(t, p) for (t, p) in entries if HEADING_RE.match(t)]
    use = chaptery if len(chaptery) >= 2 else entries
    npages = len(reader.pages)
    sections = []
    for i, (title, pg) in enumerate(use):
        start = pg + 1
        end = use[i + 1][1] if i + 1 < len(use) else npages
        sections.append({"title": title, "start_page": start, "end_page": end})
    return sections


def segment_text(text, min_words=1200, chunk_words=2500):
    """Split loose text into (title, body) sections."""
    if len(text.split()) <= min_words:
        return [("Full Document", text.strip())]
    lines = text.splitlines()
    sections, cur_title, cur = [], "Beginning", []
    for line in lines:
        h = _looks_like_heading(line)
        if h:
            if any(l.strip() for l in cur):
                sections.append((cur_title, "\n".join(cur).strip()))
            cur_title, cur = h, []
        else:
            cur.append(line)
    if any(l.strip() for l in cur):
        sections.append((cur_title, "\n".join(cur).strip()))
    sections = [(t, b) for t, b in sections if len(b.split()) >= 40]
    if len(sections) >= 2:
        return sections
    # fallback: fixed-size chunks
    words = text.split()
    return [(f"Part {i//chunk_words + 1}", " ".join(words[i:i + chunk_words]))
            for i in range(0, len(words), chunk_words)]


def target_pages(word_count):
    if word_count < 1500:
        return 3
    if word_count < 5000:
        return 4
    return 5

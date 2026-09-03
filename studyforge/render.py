"""Markdown-ish -> PDF rendering (reportlab). Kept intentionally simple and robust.

Formatting fixes (safe, additive -- table rendering intentionally untouched, see README):
  - "---" / "***" / "___" horizontal-rule lines now draw an actual thin rule instead of
    printing literal dashes.
  - "> quoted text" lines now render indented/italic instead of a literal "> " prefix.
  - "#### " and deeper heading levels no longer print a literal "#" -- they fall back to
    the nearest existing heading style instead of the generic-paragraph branch.
  - Single "*italic*" (not just "**bold**") is now converted.
  - Emoji/pictographic characters (e.g. warning/checkmark glyphs a model sometimes adds)
    are replaced with bracketed ASCII equivalents, or stripped, before rendering -- the base
    Helvetica PDF font only supports Latin-1, so unhandled ones were rendering as boxes.
"""
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

_HR_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_ITALIC_RE = re.compile(r"\*(?!\s)(.+?)(?<!\s)\*")

# Common emoji a model might add, mapped to a safe ASCII equivalent. Anything else
# outside Latin-1 is stripped by _sanitize's catch-all rather than left to render as a box.
_EMOJI_MAP = {
    "\u26A0\uFE0F": "[!] ", "\u26A0": "[!] ", "\u2705": "[OK] ", "\u274C": "[X] ", "\u2757": "[!] ",
    "\U0001F4A1": "[Tip] ", "\U0001F4DD": "[Note] ", "\U0001F4CC": "", "\U0001F511": "", "\u2B50": "", "\U0001F3AF": "",
    "\U0001F6AB": "[No] ", "\u2714\uFE0F": "check",
}


def _sanitize(s: str) -> str:
    for emoji, replacement in _EMOJI_MAP.items():
        s = s.replace(emoji, replacement)
    # Catch-all: strip anything still outside Latin-1 (WinAnsi) rather than let
    # reportlab draw an unsupported-glyph box for it.
    return "".join(ch for ch in s if ord(ch) <= 255)


def _inline(s: str) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    while s.count("**") >= 2:
        s = s.replace("**", "<b>", 1).replace("**", "</b>", 1)
    s = _ITALIC_RE.sub(r"<i>\1</i>", s)
    return s


def render_pdf(markdown_text, out_path, doc_title=""):
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=16, spaceAfter=8)
    h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13, spaceAfter=6)
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=10.5, leading=15, spaceAfter=6)
    bullet = ParagraphStyle("Bullet", parent=body, leftIndent=16, bulletIndent=6)
    quote = ParagraphStyle("Quote", parent=body, leftIndent=18, textColor=colors.HexColor("#444444"))

    doc = SimpleDocTemplate(str(out_path), pagesize=LETTER,
                            topMargin=0.8 * inch, bottomMargin=0.8 * inch,
                            leftMargin=0.9 * inch, rightMargin=0.9 * inch)
    story = []
    if doc_title:
        story += [Paragraph(_inline(_sanitize(doc_title)), h1), Spacer(1, 6)]

    for raw_line in markdown_text.splitlines():
        line = _sanitize(raw_line.rstrip())
        s = line

        if not s:
            story.append(Spacer(1, 4))
            continue

        if _HR_RE.match(s):
            story.append(HRFlowable(width="100%", thickness=0.6,
                                    color=colors.HexColor("#bbbbbb"),
                                    spaceBefore=6, spaceAfter=10))
            continue

        heading = _HEADING_RE.match(s)
        if heading:
            level, text = len(heading.group(1)), heading.group(2)
            style = h1 if level <= 2 else h2
            story.append(Paragraph(_inline(text), style))
            continue

        if s.lstrip().startswith(("- ", "* ")):
            story.append(Paragraph("\u2022 " + _inline(s.lstrip()[2:]), bullet))
            continue

        if s.lstrip().startswith(">"):
            quoted = s.lstrip()[1:].lstrip()
            story.append(Paragraph(_inline(quoted), quote))
            continue

        if s.startswith("|"):  # crude table row fallback -> plain line
            story.append(Paragraph(_inline(s.strip("|").replace("|", " \u00b7 ")), body))
            continue

        story.append(Paragraph(_inline(s), body))

    doc.build(story or [Paragraph("(empty)", body)])

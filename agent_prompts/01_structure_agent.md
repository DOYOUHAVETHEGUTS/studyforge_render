# Agent 1 — Document Structure Agent

Paste the **System prompt** into the Claude Console system field, and use the
**User message template** for the turn. In StudyForge this agent is a *fallback*:
it only runs when deterministic sectioning (PDF bookmarks / heading heuristics) is
low-confidence.

**Suggested model:** `claude-sonnet-5` · **Max tokens:** 1500 · **Temperature:** 0

---

## System prompt

```
You are a document-structure analyst for a study-material pipeline. Given the
opening excerpt of a learning resource, identify its logical top-level divisions
in reading order.

Rules:
- Output ONLY a JSON array. No prose, no markdown, no code fences.
- Each element: {"title": string, "kind": "chapter"|"unit"|"section"|"topic"}
- Prefer the resource's own division scheme (Chapters, Units, Lecciones, etc.).
- Do not invent divisions that are not evidenced in the text.
- If the excerpt shows no clear divisions, return a single element titled
  "Full Document" with kind "section".
```

## User message template

```
Material name: {{name}}
Declared type: {{material_type}}   # textbook | language | paper | reference

--- BEGIN EXCERPT ---
{{first_8000_chars_of_source}}
--- END EXCERPT ---
```

## Expected output shape

```json
[
  {"title": "Chapter 1 — Network Models", "kind": "chapter"},
  {"title": "Chapter 2 — Cabling and Topology", "kind": "chapter"}
]
```

## How to evaluate it

Feed 3–5 real documents whose true structure you know. Score: did it recover the
right number of divisions and sensible titles? If deterministic bookmark parsing
already nails these, keep this agent disabled — it exists only for messy inputs.

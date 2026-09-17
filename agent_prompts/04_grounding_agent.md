# Agent 4 — Grounding / Quality Agent

The credibility unlock. Checks that a generated summary is actually supported by the
source, and flags claims that are not. Runs right after Agent 2. This is what lets
the UI show the green "✓ Grounded to source" badge honestly.

**Suggested model:** `claude-sonnet-5` · **Max tokens:** 1500 · **Temperature:** 0

---

## System prompt

```
You are a fact-grounding checker. Determine whether each substantive claim in the
SUMMARY is supported by the SOURCE.

Respond with ONLY JSON (no prose, no code fences):

{
  "grounded": boolean,          // true if no material claim is unsupported
  "unsupported_claims": [string],  // short quotes/paraphrases of unsupported claims
  "score": number               // fraction of substantive claims supported, 0.0–1.0
}

Rules:
- Ignore generic framing sentences ("This chapter covers...") — judge only
  substantive factual claims.
- A claim is unsupported if the source neither states nor clearly implies it.
- Be strict but fair. Do not penalize correct paraphrasing or reorganization.
- If the summary adds outside facts not in the source, list them as unsupported.
```

## User message template

```
--- SOURCE ---
{{section_source_text_first_12000_chars}}

--- SUMMARY ---
{{summary_markdown}}
```

## Expected output shape

```json
{"grounded": false,
 "unsupported_claims": ["Claims SSH runs on port 23"],
 "score": 0.92}
```

## How StudyForge uses it

- `grounded == true`  → section marked grounded; green badge shown.
- `grounded == false` → section still saved, but flagged in the UI with the
  unsupported claims listed, so you can regenerate or edit before delivery.
- Toggle the whole check with `grounding_check` in `config/settings.json`.

## How to evaluate the checker itself

Build a tiny labeled set: take good summaries (should score high) and deliberately
inject 1–2 false claims (should be caught). Measure precision/recall on catching the
injected errors. This is the natural place for the optional R/RStudio evaluation
report mentioned in the assessment.

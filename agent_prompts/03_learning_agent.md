# Agent 3 — Learning / Assessment Agent

Turns an existing summary into flashcards and quiz questions. Runs after Agent 2,
reusing its output (no re-summarization). Best-effort: if it fails, the summary is
still delivered.

**Suggested model:** `claude-sonnet-5` (or `claude-haiku-4-5` to save cost) ·
**Max tokens:** 2500 · **Temperature:** 0.3

---

## System prompt

```
You create study artifacts from an existing summary. Respond with ONLY JSON in
this exact shape (no prose, no code fences):

{
  "flashcards": [{"q": string, "a": string}],
  "quiz": [{"q": string, "choices": [string, string, string, string], "answer_index": int}]
}

Rules:
- Base every card and question ONLY on the provided summary. No outside facts.
- Flashcards: one fact each, answer concise.
- Quiz: exactly 4 choices, exactly one correct, answer_index is 0-based.
- Make distractors plausible but clearly wrong to someone who read the summary.
```

## User message template

```
Make {{n_cards}} flashcards and {{n_quiz}} multiple-choice quiz questions for
"{{section_title}}".

--- SUMMARY ---
{{summary_markdown}}
```

## Expected output shape

```json
{
  "flashcards": [{"q": "Which port does IMAP4 use?", "a": "143"}],
  "quiz": [{"q": "Telnet's main weakness vs SSH?",
            "choices": ["Slower","Cleartext","No auth","IPv6 only"],
            "answer_index": 1}]
}
```

## How to evaluate it

Parse the JSON (must never fail) and check every `answer_index` is in range and the
answer is actually derivable from the summary. Reject cards that require outside
knowledge.

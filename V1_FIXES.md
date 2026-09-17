# V1 Fixes — targeted patches

These are minimal, drop-in changes to the **existing V1** (`common.py`, `gui.py`,
`daily_sender.py`, `generate_summaries.py`). They fix the four real defects found in
the assessment without rewriting anything. Apply in order. Each is copy-paste.

> The full V2 rework already incorporates all of these; these patches are for keeping
> the current V1 usable while V2 is vetted.

---

## Fix 1 — Stop email failures from silently advancing the queue

**File:** `daily_sender.py` · **Why:** V1 marks a chapter `delivered_local` on email
failure, which advances the queue — so mail can be broken for days while you assume
it's working. Leave the chapter deliverable and just save a copy + warn.

**Find:**
```python
    dest_path = dest_dir / Path(pdf_path).name
    shutil.copy(pdf_path, dest_path)

    chapter["status"] = "delivered_local"
    chapter["local_delivery_path"] = str(dest_path)
    common.save_library(library)
```
**Replace with:**
```python
    dest_path = dest_dir / Path(pdf_path).name
    shutil.copy(pdf_path, dest_path)

    # V1 FIX: do NOT advance the queue on email failure. Keep status 'generated'
    # so the next run retries this same chapter once email is fixed.
    chapter["local_delivery_path"] = str(dest_path)
    chapter["last_email_error"] = str(email_error)[:300]
    common.save_library(library)
```
Also update the warning dialog text just below it — remove the line claiming
"the queue still advanced," since it no longer does.

---

## Fix 2 — Retry the Claude call with backoff

**File:** `common.py` · **Why:** a single API timeout kills a chapter with no retry.

**Find:**
```python
def call_claude(system_prompt, user_prompt, api_key, model="claude-sonnet-5", max_tokens=8000):
    """Minimal wrapper around the Anthropic API. Requires `pip install anthropic`."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")
```
**Replace with:**
```python
def call_claude(system_prompt, user_prompt, api_key, model="claude-sonnet-5",
                max_tokens=8000, max_retries=3):
    """Minimal wrapper around the Anthropic API, with exponential backoff."""
    import time
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    last = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return "".join(b.text for b in response.content if b.type == "text")
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Claude call failed after {max_retries} attempts: {last}")
```

---

## Fix 3 — Make chapter detection tolerant (not just titles starting with "Chapter")

**File:** `gui.py` · **Why:** V1 only keeps outline entries whose title starts with
"chapter". Verified against the real uploads, that logic finds chapters in the CompTIA
books but **zero** in Hull, Natenberg, McMillan, Great Thinkers, etc. Keep all
top-level outline entries when too few "Chapter" ones exist.

**Find (inside `detect_chapters`):**
```python
            if title.lower().startswith("chapter"):
                raw.append((title, pg))
            elif raw and after_last is None and pg > raw[-1][1]:
                after_last = pg

        if not raw:
            return []
```
**Replace with:**
```python
            all_entries.append((title, pg))
            if title.lower().startswith("chapter"):
                raw.append((title, pg))
            elif raw and after_last is None and pg > raw[-1][1]:
                after_last = pg

        # V1 FIX: if we didn't find enough "Chapter N" entries, fall back to using
        # every top-level bookmark as a section (works for trade/finance/other books).
        if len(raw) < 2 and len(all_entries) >= 2:
            raw = all_entries
            after_last = None

        if not raw:
            return []
```
And add one line where the loop variables are initialised (just before the `for item`
loop):
```python
        all_entries = []   # every top-level (title, page), used as fallback
```

---

## Fix 4 — Record failures instead of silently skipping

**File:** `generate_summaries.py` · **Why:** an empty/failed chapter is silently
`continue`d, so the run *looks* complete. Mark it `failed` and keep going.

**Find:**
```python
        text = common.get_source_text(tb, chapter)
        if not text.strip():
            print(f"  WARNING: no extractable text for {chapter['title']}, skipping.")
            continue
```
**Replace with:**
```python
        try:
            text = common.get_source_text(tb, chapter)
        except Exception as e:  # noqa: BLE001
            print(f"  FAILED extract {chapter['title']}: {e}")
            chapter["status"] = "failed"
            chapter["error"] = str(e)[:300]
            continue
        if not text.strip():
            print(f"  FAILED (no text) {chapter['title']} — likely scanned PDF.")
            chapter["status"] = "failed"
            chapter["error"] = "no extractable text (scanned?)"
            continue
```
Then wrap the summarize + render block for that chapter in a `try/except` that sets
`chapter["status"] = "failed"` on error, so one bad chapter never aborts the book.

---

## Fix 5 (optional) — Portable paths

**File:** `common.py` + `config/library.json` · **Why:** V1 stores absolute Windows
paths (`D:\notes\...`), so the library only works on the original machine.

Add these helpers to `common.py`:
```python
def rel_path(p):
    """Store paths relative to the project dir."""
    from pathlib import Path
    try:
        return str(Path(p).resolve().relative_to(BASE_DIR))
    except ValueError:
        return str(p)

def abs_path(p):
    from pathlib import Path
    p = Path(p)
    return p if p.is_absolute() else (BASE_DIR / p)
```
Then: write paths with `rel_path(...)` (in `generate_summaries.py` and `gui.py`), and
read them with `common.abs_path(...)` wherever a stored path is opened
(`get_source_text`, `daily_sender` when reading `summary_pdf_path`). For an existing
`library.json`, either regenerate or run a one-off script that rewrites each stored
path through `rel_path`.

---

## After applying

```bash
python generate_summaries.py --textbook-id <id>   # failures now visible, retried
python daily_sender.py                            # queue no longer skips on email fail
python ssl_diagnostic.py                          # still the fastest email triage
```

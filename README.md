# StudyForge

Turn textbooks, papers, and language workbooks into grounded, exam-focused study
summaries — then drip them to your inbox one section at a time. A Python rework of a
Tkinter prototype into a deployable, agent-based pipeline with a web dashboard.

> **Problem → V1 → V2:** V1 was a local Tkinter app with brittle chapter detection,
> hard-coded Windows paths, silent email failures, and one-shot (ungrounded)
> summaries. V2 keeps the good idea — separate *expensive generation* from *cheap
> daily delivery* — and adds a four-agent pipeline, grounding checks, material-type
> awareness, portable storage, visible failure handling, and a FastAPI UI.

## Architecture

```
Add material ─▶ Ingest ─▶ Structure ─▶ [ per section ] ─▶ Outputs ─▶ Deliver
 (pdf/docx/       (extract   (material-      Agent 2 summarize    md+pdf     one section
  txt/url)         text,      type +          Agent 4 ground      +json      per run,
                   scanned    section map)    Agent 3 learn                  email or
                   gate)                      (retry+isolate)                local fallback
```

- **Agent 1 — Structure** (`agents.structure_agent`): fallback section mapping when
  bookmarks/heuristics are weak.
- **Agent 2 — Summarize** (`agents.summarize_agent`): grounded study summary; separate
  strategy for language material.
- **Agent 3 — Learning** (`agents.learning_agent`): flashcards + quiz from the summary.
- **Agent 4 — Grounding** (`agents.grounding_agent`): verifies the summary is supported
  by the source; flags unsupported claims.

Deterministic Python does the rest (extraction, bookmark parsing, chunking, PDF
rendering, email, DB). Agents run **sequentially per section**, **isolated** so one
failure never sinks the book, with **retry + backoff** and explicit `failed` status.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # add your keys
cp config/settings.example.json config/settings.json  # set email_address / to_email
set -a; source .env; set +a     # export the env vars (Windows: set them via setx)
```

Get an API key at https://console.anthropic.com. For Gmail delivery, create an
[App Password](https://myaccount.google.com/apppasswords) and put it in
`STUDYFORGE_EMAIL_APP_PASSWORD`.

## Test run (CLI)

```bash
# add a textbook and detect chapters from its PDF bookmarks
python -m studyforge.cli add "Network+" --pdf book.pdf --exam "CompTIA Network+ N10-009"

# add language material (different processing strategy)
python -m studyforge.cli add "Spanish A1" --file aula1.pdf --type language

python -m studyforge.cli list
python -m studyforge.cli process <material_id>        # the expensive step
python -m studyforge.cli process <material_id> --failed-only
python -m studyforge.cli send <material_id>           # deliver next section
python -m studyforge.cli diagnose-email               # explain SMTP/TLS failures
```

No API key handy? The smoke test stubs the LLM and needs no network:

```bash
python tests/test_smoke.py
```

## Web dashboard

```bash
python -m studyforge.cli serve       # http://127.0.0.1:8000
```

Everything is configured from the browser now — no env vars or file editing required
for day-to-day use:

- **Add Material** (top of the Library page) — drag-and-drop or pick a file
  (.pdf/.docx/.txt/.md), optional name/type/exam/focus, upload progress, and a clear
  error if the file can't be read (unsupported type, scanned PDF, etc.).
- **Settings** — Account, AI provider (API key, model, grounding/flashcards toggles),
  Email (SMTP + app password, with a one-click TLS diagnostic link), and Processing
  preferences (summary depth, output format).
- A **readiness banner** on the Library page tells you exactly what's missing
  ("AI provider not configured" / "Email not configured") before you try to process
  anything, and `/process` itself refuses to start with a clear message rather than
  failing silently mid-run.

Environment variables (`ANTHROPIC_API_KEY`, `STUDYFORGE_EMAIL_APP_PASSWORD`) still
work and take priority if set — useful for deployment — but are no longer required
for local use.

### Where secrets live

- Non-secret settings (model choice, SMTP host, processing preferences) → `config/settings.json`.
- API key and email app password → `data/secrets.json`, a **separate** file, `chmod 600`
  where the OS supports it, gitignored, never rendered to the browser in full (only a
  last-4-characters mask), never logged, never referenced from frontend JS.
- This is a **single-user, no-auth local app** — there's one settings/secrets store, not
  per-user. That's a deliberate MVP scope call (see `config.py` docstring on
  `save_secrets`), not an oversight: adding real multi-user support means a proper
  per-user encrypted credential store and auth, which is out of scope until the app
  actually needs more than one user.

### Viewing summaries

Click any generated section's title in the book detail page to open its PDF inline in
the browser (not a download) — works on desktop and mobile.

### Scheduled delivery

Settings → Scheduled delivery lets you set a daily or weekly cadence (time of day, and
day of week for weekly) for sending the next queued section from your active material —
equivalent to clicking "Send next to inbox" automatically.

**This runs as a background loop inside the app process** — it only fires while
`python -m studyforge.cli serve` is running. It is not a system cron. If you close the
app, scheduled sends pause until you reopen it. For delivery that must happen on a
schedule regardless of whether the app is open, use your OS's scheduler instead:

```bash
# Windows: Task Scheduler -> Action: python -m studyforge.cli send, Trigger: daily
# macOS/Linux: cron entry, e.g. run at 9am daily
0 9 * * * cd /path/to/studyforge && python -m studyforge.cli send
```



## Email: the V1 fix

V1's email "silently stopped working." Diagnosed cause: TLS certificate verification
failing on `starttls()` (Windows OS cert store / TLS inspection), not auth. V2:
pins the `certifi` CA bundle, ships `diagnose-email`, and **does not advance the queue
on failure** (`advance_queue_on_email_failure: false`) so failures are visible. If a
corporate proxy does TLS inspection, switch to an HTTPS API sender.

## Storage

Everything lives under `data/` (gitignored): `uploads/`, `extracted/`, `summaries/`,
`unsent/`, and `studyforge.db` (SQLite). Paths in the DB are **relative to the repo
root**, so the project is portable.

## Deploy

Runs anywhere Python does. For a public demo: `uvicorn studyforge.web:app` behind a
reverse proxy, or a container. Keep secrets in environment variables.

## Agent prompts

`agent_prompts/*.md` are the four agents formatted for the Claude Console so you can
iterate on each prompt independently of the code.

## Deploying to Render (share with others)

StudyForge is a stateful Python server, so it needs a host that runs a long-lived process
with a writable disk — **not** a static host like Netlify. Render works with the included
`Dockerfile` and `render.yaml`.

1. Push this repo to GitHub (already done if you followed the earlier steps).
2. In Render: **New + → Blueprint**, pick this repo. Render reads `render.yaml` and creates a
   Docker web service with a 1 GB persistent disk mounted at `/data`.
3. When prompted, set the environment variables (their values are NOT stored in the repo):
   - `ANTHROPIC_API_KEY` — your key (all usage bills to it).
   - `STUDYFORGE_ACCESS_PASSWORD` — a shared password you give friends. Anyone hitting the
     site must enter it before using the app. **Leave it unset to run fully open (not
     recommended for a public URL).**
   - `STUDYFORGE_EMAIL_APP_PASSWORD` — only if you want email delivery.
4. Deploy. Render gives you `https://studyforge-xxxx.onrender.com`. Share that URL + the
   password. To use a subdomain like `studyforge.keandre.app`, add it under the service's
   **Custom Domains** and point a CNAME at Render.

**Access control:** the password gate is enforced by middleware for every page and API route
when `STUDYFORGE_ACCESS_PASSWORD` is set. It's a single shared password (fine for a few
friends), not per-user accounts. Everyone shares the same library/data.

**Cost note:** every summary, interview prep, and language assessment spends *your* Anthropic
credits. The password gate keeps the open internet out, but friends with the password are
still spending your balance — watch your usage in the Anthropic console.

**Free tier:** to trial without the ~$7/mo disk, change `plan: starter` to `plan: free` and
remove the `disk:` block in `render.yaml`. The app runs, but uploads/generated files/progress
reset whenever Render restarts the instance (including its daily idle spin-down).

**Local use is unchanged:** with none of these env vars set, `python -m studyforge.cli serve`
behaves exactly as before — open, data under `./data`.

# StudyForge — Key Changes Since the Tkinter Version

*A one-page summary of what changed between the original desktop prototype and the current
web-based system.*

## Architecture

| | **V1 (Tkinter)** | **Current (FastAPI web app)** |
|---|---|---|
| Interface | Local desktop GUI window | Browser dashboard — Library, Book detail, Settings, all in-app |
| Storage | `config/library.json`, absolute Windows paths (`D:\notes\...`) | SQLite (`data/studyforge.db`), paths relative to project root — portable |
| Configuration | Hand-edit JSON files, no in-app settings | Full **Settings** page: Account, AI, Email, Processing, Custom Agent Prompts |
| Adding material | Manually place files, edit config | **Add Material** button — drag/drop upload, live progress, clear errors |
| Secrets | None managed — env vars only, no masking | Separate `data/secrets.json` (chmod 600, gitignored), masked in UI, never sent to browser in full |

## The AI pipeline: one call → four agents

V1 made a single one-shot summarization call per chapter with no verification step. The current
system runs **four specialized agents** per section, each swappable independently:

1. **Structure** — maps document sections (fallback for low-confidence sectioning; not yet
   auto-triggered by the pipeline — a known gap, not a hidden one)
2. **Summarize** — writes the grounded study summary (separate strategies for textbook,
   language-learning, and narrative/fiction material)
3. **Learning** — generates flashcards + quiz questions from the summary
4. **Grounding** — checks every summary against the source and flags unsupported claims

Each of the four now has an optional **custom-prompt override** in Settings, set two ways:
paste a prompt directly, or paste a **Claude Managed Agents** `agent_...` ID and click
**Sync** — StudyForge fetches that agent's system prompt via the Managed Agents API
and fills it in automatically. Either way, StudyForge still runs the fast, stateless
Messages API per section — it does not run your agents as full Managed Agent sessions
(that runtime is built for tool-using/sandboxed work and bills per session-hour, which
buys nothing for a single completion call).

## Reliability

- **Chapter detection**: V1 only recognized bookmarks titled literally "Chapter N" — validated
  against real textbooks, this found **zero** chapters in several non-CompTIA books. The current
  detector accepts any top-level bookmark structure, with a text-segmentation fallback.
- **Failures are visible, not silent**: a failed section is marked `failed` with a reason, isolated
  from the rest of the book, and re-runnable on its own (`process failed-only`). V1 could skip a
  chapter silently and still report the run as complete.
- **Retries**: model calls now retry with exponential backoff (V1 had none).
- **Email**: diagnosed the actual V1 failure (TLS certificate handling, not a config typo), added a
  one-command diagnostic, and — since some networks intercept SMTP entirely via antivirus/VPN
  TLS inspection — added a second delivery backend (**Resend**, plain HTTPS) as an alternative to
  SMTP, selectable in Settings.
- **Queue behavior**: V1 advanced the delivery queue even when email failed, hiding the failure.
  The current system does not, by default — failed sends save locally and stay in the queue.

## Output quality

- PDF rendering now handles horizontal rules, blockquotes, deeper heading levels, and single-
  asterisk italics correctly — V1's renderer left raw markdown syntax (`---`, `> `, `####`) visible as
  literal text in the output, and unsupported emoji rendered as broken glyph boxes.

## Studying, not just generating
- **Clickable in-browser summaries**: click any completed section to open its PDF inline.
- **Flashcards + quiz UI**: Agent 3's output (previously only a `.json` file with no viewer) is now
  a real study page — flip-card review and a scored multiple-choice quiz, per section.
- **Exam-goal progress tracking**: when a material has an exam name set, its page shows a
  progress panel framed around that goal — % of sections read, average quiz score, and how many
  sections have been quizzed. Quiz results persist (best score + attempt count) in the database,
  survives an existing DB via a self-healing migration.

## What's still a known limitation, not a hidden gap

- Single-user, no-auth app by design (documented MVP scope call, not an oversight).
- Agent 1 (Structure) exists and is fully wired for manual/future use but isn't yet auto-triggered
  by the pipeline when deterministic sectioning is weak.
- Table-style content in source PDFs (using `·`-separated pseudo-tables rather than markdown
  pipe tables) still renders as plain text rather than a formatted grid — a larger, deliberately
  unstarted change pending confirmation it won't destabilize other output types.

## Interview prep (new, separate from the study pipeline)

- A dedicated **Interview Prep** section (its own page + nav link, not a material type).
- Takes three inputs — a **job posting**, your **résumé**, and free-text **notes** — each of
  which can be pasted or uploaded (.pdf/.docx/.txt/.md, extracted with the existing ingest code).
- Two direct Messages API calls (same engine as the rest of the app, no Managed Agent sessions):
  one produces a structured prep document (what the role tests, your proof points mapped to the
  posting's requirements, concept refresher, likely questions, a live-case framework, a closing
  line); the other produces a set of likely questions with suggested approaches.
- Output is **both a PDF** (via the existing renderer) **and an interactive study page** — a
  flip-card rehearsal deck plus a full question list.
- CLI: `interview-add "<role>" --job-file posting.pdf --resume-file resume.pdf` then
  `interview-generate <id>`.

## Languages (new, stateful language-learning coach)

A persistent language module — deliberately *not* a document summarizer with a language skin.
The architectural spine is the split between **instructional** data (what CAN be taught) and
**assessment** data (what SHOULD be taught next):

- **Intake wizard** → language, goal, frequency, target date, self-level, free-text ability.
- **Materials** (`lang_materials`): upload a digital textbook (.pdf/.docx/.txt/.md) *or photos of
  handwritten notes* (.png/.jpg, read via image understanding, flagged `needs_review` when
  extraction is uncertain). Analyzed into a **structured curriculum** (units/grammar/vocab), not
  a summary.
- **Diagnostic + adaptive path**: estimates per-skill level, picks a language-appropriate
  framework (CEFR/JLPT/TOPIK/…), and builds priorities → current unit → upcoming → review.
  Current-unit concepts seed a **learner model** (`lang_concepts`) with a simple, reliable spaced-
  review model (confidence + review priority updated per attempt).
- **Speaking assessment**: browser microphone recording per prompt (upload fallback), audio
  stored under `data/` (never in the public static dir), plus a transcript box. A reusable scoring
  engine grades **Fluency & Coherence, Lexical Resource, Grammatical Range & Accuracy, and
  Pronunciation** on a 0–9 practice scale, with feedback and recurring errors fed back into the
  review queue. **Writing assessment** reuses the same engine.
- **Honest safeguards**: levels/scores are always labeled *StudyForge estimate / Practice Score*,
  never an official JLPT/TOPIK/CEFR/IELTS result; and pronunciation is scored **only** when the
  learner confirms spoken audio — from transcript alone it returns null and says so.
- **Learning plan PDF export** via the existing renderer. Multi-language by design (one profile
  per language, each with independent path/materials/concepts/assessments).

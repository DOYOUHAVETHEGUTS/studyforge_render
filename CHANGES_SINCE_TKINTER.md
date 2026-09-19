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

## Case studies (new, nested inside Interview Prep)

Practice case-interview scenarios generated from a specific job posting, displayed inside that
interview prep's own page (not a separate top-level section — a case study is inherently tied to
one job posting/résumé pairing).

- **Role-tailored type suggestions**: the AI reads the job posting and proposes 4-5 case types
  that actually fit the role (e.g. partnership evaluation, market sizing, data/operations — not
  generic consulting cases if the role doesn't call for them), plus an **easier/medium/harder**
  difficulty selector. You can also base a case on a *different* interview prep's job posting you
  uploaded earlier, without leaving the current one.
- **Multiple valid approaches, not one rigid answer key**: every question carries 2-3 different
  legitimate solution approaches. The printable **PDF** (scenario for live practice, then a
  page-break, then a "Potential Ways to Answer" section) reflects this, and so does scoring —
  the grader checks which approach the candidate's answer aligns with (or credits a reasonable
  approach not listed), rather than requiring a verbatim match.
- **Live timed run**: 20/30/60-minute or untimed, in-browser. When the timer hits zero it's a
  **soft stop** — nothing is disabled or auto-submitted, your answers stay exactly as typed, and
  you choose to keep going or submit as-is.
- **Feedback per question**: `correct` (brief note on what satisfied the answer), `partial`, or
  `incorrect` (detailed feedback on what a strong answer would have covered), grounded in the
  job posting.
- **Résumé-grounded gap analysis**: cross-references the candidate's résumé against the case and
  job posting to call out real, textually-supported experience gaps — not speculation.

Verified end-to-end (15 checks): type suggestion, generation with real multi-approach solutions,
PDF page-break + solutions content confirmed via PDF text extraction, timed/untimed attempt
creation, invalid-duration rejection, correct/partial scoring with matched/missing points,
resume-grounded gaps, cross-prep job-posting sourcing, and cascade-delete of attempts.

## Case studies (nested inside Interview Prep)

- Lives inside each interview prep's detail page, not a separate section — a case study
  only makes sense tied to a specific job posting/résumé.
- **Both case type and difficulty are independently selectable.** Case types are
  suggested dynamically per role (not a hardcoded list) — e.g. an Alliances Analytics
  posting surfaces "Partnership Evaluation" and "Data & Operations" cases, not generic
  consulting cases. Difficulty (Easier/Medium/Harder) adjusts ambiguity and structure.
- Can base the case on the current prep's job posting, a **different saved prep** you've
  already uploaded, via a dropdown.
- **PDF**: practice-facing scenario + questions (no answers), a page break, then a
  "Potential Ways to Answer" page. Every question carries 2–3 *different valid approaches*
  with key points — not one rigid answer, matching how real case interviews are graded.
- **Live timed run**: 20/30/60-minute soft timer. At zero, it stops counting and shows a
  banner — nothing is disabled or auto-submitted, so no typed work is ever lost. The
  candidate chooses to keep going or submit as-is.
- **Scoring**: per-question verdict (correct/partial/incorrect) checked against whichever
  approach the answer aligns with. Correct → a brief note on what satisfied it. Wrong →
  detailed feedback on what a strong answer would have covered. Résumé-grounded gaps are
  called out only with real textual support, not speculation.

## Offer comparison (nested under Interview Prep)

- Entry point sits on the Interview Prep page — it's the same job-search workflow, but
  compares *across* opportunities instead of preparing for one.
- Add 2+ offers, each with a job description (pasted or uploaded/extracted), compensation,
  benefits, relocation, and free-text "other considerations". Résumé and a "what matters to
  you" field are captured once and shared across the comparison.
- **Questions are a deliberate mix**: general priority questions (comp vs growth, relocation
  tolerance, risk appetite) *and* offer-specific ones tied to a concrete detail of one named
  offer. Each carries a "why it matters" line so the candidate sees the tradeoff being probed.
- **Ranking** orders offers best→least attractive *for this candidate*, with pros, cons, a
  fit score, and a rationale per offer. Facts must trace to the offers/résumé/answers —
  genuinely missing information is surfaced as `unknowns` ("equity value not provided")
  rather than guessed at, and the UI frames the result as a structured second opinion, not
  a decision.
- Answers persist, so the candidate can revise them and re-rank.

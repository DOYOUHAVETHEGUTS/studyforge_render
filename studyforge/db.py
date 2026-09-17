"""
SQLite storage layer (replaces V1's library.json).

Two tables: materials (books/papers/etc.) and sections (chapters/units).
Section status is the backbone of visible-failure handling:
  pending -> extracting -> summarizing -> generated -> failed
Delivery status:  none -> sent | delivered_local
"""
import sqlite3
import uuid
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS materials (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    material_type TEXT NOT NULL DEFAULT 'textbook',   -- textbook|language|paper|reference
    exam_name     TEXT DEFAULT '',
    objectives    TEXT DEFAULT '',
    focus         TEXT DEFAULT '',
    source_path   TEXT DEFAULT '',
    source_ref    TEXT DEFAULT '',
    active        INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS sections (
    id            TEXT PRIMARY KEY,
    material_id   TEXT NOT NULL,
    ordinal       INTEGER NOT NULL,
    title         TEXT NOT NULL,
    text_path     TEXT DEFAULT '',
    start_page    INTEGER,
    end_page      INTEGER,
    status        TEXT DEFAULT 'pending',
    delivery      TEXT DEFAULT 'none',
    summary_md    TEXT DEFAULT '',
    summary_pdf   TEXT DEFAULT '',
    grounded      INTEGER DEFAULT 0,
    unsupported   TEXT DEFAULT '',
    error         TEXT DEFAULT '',
    quiz_best_score REAL,
    quiz_attempts INTEGER DEFAULT 0,
    updated_at    TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE
);
"""

# Lightweight self-healing migration: CREATE TABLE IF NOT EXISTS above only applies to
# brand-new DBs. For a DB file created before quiz tracking existed, add the columns
# here; SQLite raises OperationalError if a column already exists, which we ignore.
_MIGRATIONS = [
    "ALTER TABLE sections ADD COLUMN quiz_best_score REAL",
    "ALTER TABLE sections ADD COLUMN quiz_attempts INTEGER DEFAULT 0",
]

INTERVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS interview_preps (
    id            TEXT PRIMARY KEY,
    role_title    TEXT NOT NULL,
    company       TEXT DEFAULT '',
    job_posting   TEXT DEFAULT '',        -- extracted/pasted job posting text
    resume_text   TEXT DEFAULT '',        -- extracted/pasted resume text
    notes         TEXT DEFAULT '',        -- free-text notes from the user
    status        TEXT DEFAULT 'pending', -- pending -> generating -> generated -> failed
    prep_md       TEXT DEFAULT '',        -- generated markdown
    prep_pdf      TEXT DEFAULT '',        -- rendered PDF (relative path)
    questions_json TEXT DEFAULT '',       -- JSON list of {q, approach} for the study page
    error         TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);
"""

# Language learning: a persistent, stateful module. The key architectural line the user
# emphasized is INSTRUCTIONAL vs ASSESSMENT data:
#   - lang_materials  = what CAN be taught (textbooks, notes, syllabi) -> instructional
#   - lang_concepts   = the learner model: what SHOULD be taught next (spaced review state)
#   - lang_assessments = evidence of production (speaking/writing) driving the model
LANGUAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS lang_profiles (
    id            TEXT PRIMARY KEY,
    language      TEXT NOT NULL,
    goal          TEXT DEFAULT '',
    frequency     TEXT DEFAULT '',
    target_date   TEXT DEFAULT '',
    level_self    TEXT DEFAULT '',        -- learner's self-reported starting level
    context       TEXT DEFAULT '',        -- free-text "tell us about your ability"
    framework     TEXT DEFAULT '',        -- CEFR | JLPT | TOPIK | generic ... (AI-chosen)
    est_level     TEXT DEFAULT '',        -- StudyForge estimated level (NOT official)
    diagnostic_json TEXT DEFAULT '',      -- per-skill estimates + rationale
    path_json     TEXT DEFAULT '',        -- adaptive learning path (priorities/units/review)
    path_pdf      TEXT DEFAULT '',        -- exported plan PDF (relative path)
    status        TEXT DEFAULT 'intake',  -- intake -> diagnosed -> active
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS lang_materials (
    id            TEXT PRIMARY KEY,
    profile_id    TEXT NOT NULL,
    kind          TEXT DEFAULT 'textbook', -- textbook | notes_photo | syllabus | other
    name          TEXT DEFAULT '',
    source_path   TEXT DEFAULT '',
    curriculum_json TEXT DEFAULT '',       -- structured units/grammar/vocab (not a summary)
    status        TEXT DEFAULT 'pending',  -- pending -> analyzed -> failed
    needs_review  INTEGER DEFAULT 0,       -- 1 when OCR/handwriting extraction is uncertain
    error         TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (profile_id) REFERENCES lang_profiles(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS lang_concepts (
    id            TEXT PRIMARY KEY,
    profile_id    TEXT NOT NULL,
    concept       TEXT NOT NULL,
    category      TEXT DEFAULT 'grammar',  -- grammar|vocabulary|reading|writing|listening|speaking|pronunciation|culture
    status        TEXT DEFAULT 'new',      -- new | learning | review | mastered
    attempts      INTEGER DEFAULT 0,
    correct       INTEGER DEFAULT 0,
    confidence    REAL DEFAULT 0,          -- 0..1 rolling performance
    review_priority INTEGER DEFAULT 3,     -- 1 (soon) .. 5 (rare); drives the review queue
    last_practiced TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (profile_id) REFERENCES lang_profiles(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS lang_assessments (
    id            TEXT PRIMARY KEY,
    profile_id    TEXT NOT NULL,
    kind          TEXT DEFAULT 'speaking', -- speaking | writing
    prompts_json  TEXT DEFAULT '',         -- generated prompts
    responses_json TEXT DEFAULT '',        -- transcripts / written text + per-item feedback
    audio_dir     TEXT DEFAULT '',         -- relative dir holding recordings (speaking)
    score_fluency REAL,
    score_lexical REAL,
    score_grammar REAL,
    score_pronunciation REAL,
    score_overall REAL,
    audio_based   INTEGER DEFAULT 0,       -- 0 = scored from transcript only (be honest)
    feedback_json TEXT DEFAULT '',         -- strengths / corrections / one thing to improve
    recurring_json TEXT DEFAULT '',        -- recurring errors fed back into the model
    status        TEXT DEFAULT 'created',  -- created -> submitted -> scored -> failed
    error         TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (profile_id) REFERENCES lang_profiles(id) ON DELETE CASCADE
);
"""


@contextmanager
def conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db():
    with conn() as c:
        c.executescript(SCHEMA)
        c.executescript(INTERVIEW_SCHEMA)
        c.executescript(LANGUAGE_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists


def new_id() -> str:
    return uuid.uuid4().hex[:10]


# ---- materials -----------------------------------------------------------
def add_material(name, material_type="textbook", **kw) -> str:
    mid = new_id()
    with conn() as c:
        c.execute(
            """INSERT INTO materials
               (id,name,material_type,exam_name,objectives,focus,source_path,source_ref)
               VALUES (?,?,?,?,?,?,?,?)""",
            (mid, name, material_type, kw.get("exam_name", ""), kw.get("objectives", ""),
             kw.get("focus", ""), kw.get("source_path", ""), kw.get("source_ref", "")),
        )
    return mid


def list_materials():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM materials ORDER BY created_at DESC")]


def get_material(mid):
    with conn() as c:
        r = c.execute("SELECT * FROM materials WHERE id=?", (mid,)).fetchone()
        return dict(r) if r else None


def set_active(mid):
    with conn() as c:
        c.execute("UPDATE materials SET active=0")
        c.execute("UPDATE materials SET active=1 WHERE id=?", (mid,))


def get_active():
    with conn() as c:
        r = c.execute("SELECT * FROM materials WHERE active=1 LIMIT 1").fetchone()
        if not r:
            r = c.execute("SELECT * FROM materials ORDER BY created_at LIMIT 1").fetchone()
        return dict(r) if r else None


def delete_material(mid):
    with conn() as c:
        c.execute("DELETE FROM materials WHERE id=?", (mid,))


# ---- sections ------------------------------------------------------------
def add_section(material_id, ordinal, title, **kw) -> str:
    sid = new_id()
    with conn() as c:
        c.execute(
            """INSERT INTO sections (id,material_id,ordinal,title,text_path,start_page,end_page)
               VALUES (?,?,?,?,?,?,?)""",
            (sid, material_id, ordinal, title, kw.get("text_path", ""),
             kw.get("start_page"), kw.get("end_page")),
        )
    return sid


def get_sections(material_id):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM sections WHERE material_id=? ORDER BY ordinal", (material_id,))]


def get_section(sid):
    with conn() as c:
        r = c.execute("SELECT * FROM sections WHERE id=?", (sid,)).fetchone()
        return dict(r) if r else None


def update_section(sid, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [sid]
    with conn() as c:
        c.execute(f"UPDATE sections SET {cols}, updated_at=datetime('now') WHERE id=?", vals)


def next_undelivered(material_id):
    with conn() as c:
        r = c.execute(
            "SELECT * FROM sections WHERE material_id=? AND status='generated' "
            "AND delivery='none' ORDER BY ordinal LIMIT 1", (material_id,)).fetchone()
        return dict(r) if r else None


def record_quiz_result(sid, correct, total):
    """Store the best-ever score for this section's quiz (fraction 0..1), and bump attempts."""
    if total <= 0:
        return
    score = correct / total
    with conn() as c:
        row = c.execute("SELECT quiz_best_score, quiz_attempts FROM sections WHERE id=?",
                        (sid,)).fetchone()
        if not row:
            return
        best = row["quiz_best_score"]
        new_best = score if best is None else max(best, score)
        c.execute("UPDATE sections SET quiz_best_score=?, quiz_attempts=quiz_attempts+1, "
                 "updated_at=datetime('now') WHERE id=?", (new_best, sid))


def stats(material_id):
    secs = get_sections(material_id)
    total = len(secs)
    done = sum(1 for s in secs if s["status"] == "generated")
    failed = sum(1 for s in secs if s["status"] == "failed")
    sent = sum(1 for s in secs if s["delivery"] == "sent")
    local = sum(1 for s in secs if s["delivery"] == "delivered_local")
    attempted = [s for s in secs if (s["quiz_attempts"] or 0) > 0]
    avg_quiz_score = (sum(s["quiz_best_score"] or 0 for s in attempted) / len(attempted)
                      if attempted else None)
    return {"total": total, "done": done, "failed": failed, "sent": sent, "local": local,
            "quiz_attempted": len(attempted), "avg_quiz_score": avg_quiz_score}


# ---- interview preps -----------------------------------------------------
def add_interview_prep(role_title, company="", job_posting="", resume_text="", notes="") -> str:
    iid = new_id()
    with conn() as c:
        c.execute(
            """INSERT INTO interview_preps
               (id, role_title, company, job_posting, resume_text, notes)
               VALUES (?,?,?,?,?,?)""",
            (iid, role_title, company, job_posting, resume_text, notes),
        )
    return iid


def list_interview_preps():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM interview_preps ORDER BY created_at DESC")]


def get_interview_prep(iid):
    with conn() as c:
        r = c.execute("SELECT * FROM interview_preps WHERE id=?", (iid,)).fetchone()
        return dict(r) if r else None


def update_interview_prep(iid, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [iid]
    with conn() as c:
        c.execute(f"UPDATE interview_preps SET {cols}, updated_at=datetime('now') WHERE id=?", vals)


def delete_interview_prep(iid):
    with conn() as c:
        c.execute("DELETE FROM interview_preps WHERE id=?", (iid,))


# ---- language profiles ---------------------------------------------------
def add_lang_profile(language, **kw) -> str:
    pid = new_id()
    with conn() as c:
        c.execute(
            """INSERT INTO lang_profiles
               (id, language, goal, frequency, target_date, level_self, context)
               VALUES (?,?,?,?,?,?,?)""",
            (pid, language, kw.get("goal", ""), kw.get("frequency", ""),
             kw.get("target_date", ""), kw.get("level_self", ""), kw.get("context", "")))
    return pid


def list_lang_profiles():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM lang_profiles ORDER BY updated_at DESC")]


def get_lang_profile(pid):
    with conn() as c:
        r = c.execute("SELECT * FROM lang_profiles WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None


def update_lang_profile(pid, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE lang_profiles SET {cols}, updated_at=datetime('now') WHERE id=?",
                  list(fields.values()) + [pid])


def delete_lang_profile(pid):
    with conn() as c:
        c.execute("DELETE FROM lang_profiles WHERE id=?", (pid,))


# ---- language materials --------------------------------------------------
def add_lang_material(profile_id, kind, name, source_path="") -> str:
    mid = new_id()
    with conn() as c:
        c.execute("""INSERT INTO lang_materials (id, profile_id, kind, name, source_path)
                     VALUES (?,?,?,?,?)""", (mid, profile_id, kind, name, source_path))
    return mid


def get_lang_materials(profile_id):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM lang_materials WHERE profile_id=? ORDER BY created_at", (profile_id,))]


def get_lang_material(mid):
    with conn() as c:
        r = c.execute("SELECT * FROM lang_materials WHERE id=?", (mid,)).fetchone()
        return dict(r) if r else None


def update_lang_material(mid, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE lang_materials SET {cols} WHERE id=?", list(fields.values()) + [mid])


# ---- language concepts (learner model / spaced review) -------------------
def add_lang_concept(profile_id, concept, category="grammar", status="new", review_priority=3):
    cid = new_id()
    with conn() as c:
        c.execute("""INSERT INTO lang_concepts
                     (id, profile_id, concept, category, status, review_priority)
                     VALUES (?,?,?,?,?,?)""",
                  (cid, profile_id, concept, category, status, review_priority))
    return cid


def get_lang_concepts(profile_id):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM lang_concepts WHERE profile_id=? ORDER BY review_priority, concept",
            (profile_id,))]


def record_concept_result(cid, correct):
    """Update rolling confidence + review priority from a practice attempt.
    Simple, reliable review model (not full SM-2): confidence is a running ratio;
    priority tightens when missed, relaxes when consistently correct."""
    with conn() as c:
        r = c.execute("SELECT attempts, correct, confidence FROM lang_concepts WHERE id=?",
                      (cid,)).fetchone()
        if not r:
            return
        attempts = (r["attempts"] or 0) + 1
        correct_n = (r["correct"] or 0) + (1 if correct else 0)
        confidence = correct_n / attempts
        if confidence >= 0.85 and attempts >= 3:
            status, priority = "mastered", 5
        elif confidence >= 0.6:
            status, priority = "review", 3
        else:
            status, priority = "learning", 1
        c.execute("""UPDATE lang_concepts SET attempts=?, correct=?, confidence=?,
                     status=?, review_priority=?, last_practiced=datetime('now') WHERE id=?""",
                  (attempts, correct_n, confidence, status, priority, cid))


# ---- language assessments ------------------------------------------------
def add_lang_assessment(profile_id, kind, prompts_json="", audio_dir="") -> str:
    aid = new_id()
    with conn() as c:
        c.execute("""INSERT INTO lang_assessments (id, profile_id, kind, prompts_json, audio_dir)
                     VALUES (?,?,?,?,?)""", (aid, profile_id, kind, prompts_json, audio_dir))
    return aid


def get_lang_assessment(aid):
    with conn() as c:
        r = c.execute("SELECT * FROM lang_assessments WHERE id=?", (aid,)).fetchone()
        return dict(r) if r else None


def get_lang_assessments(profile_id, kind=None):
    q = "SELECT * FROM lang_assessments WHERE profile_id=?"
    args = [profile_id]
    if kind:
        q += " AND kind=?"
        args.append(kind)
    q += " ORDER BY created_at"
    with conn() as c:
        return [dict(r) for r in c.execute(q, args)]


def update_lang_assessment(aid, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE lang_assessments SET {cols} WHERE id=?", list(fields.values()) + [aid])


def delete_lang_assessment(aid):
    with conn() as c:
        c.execute("DELETE FROM lang_assessments WHERE id=?", (aid,))

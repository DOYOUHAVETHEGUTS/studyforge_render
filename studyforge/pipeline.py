"""
Pipeline orchestration.

  add_material(...)     ingest + material-type + section map -> DB (cheap, no LLM)
  process_material(...) per-section agent chain (Agent 2 -> 4 -> 3) with retries,
                        checkpointing, and explicit 'failed' status (no silent fails)
  send_next(...)        deliver one generated-but-unsent section; on email failure,
                        save locally and (by default) DO NOT advance the queue

Design kept from V1: expensive generation and cheap delivery are separate phases.
"""
import json
import shutil
from pathlib import Path

from . import config, db, email_delivery, ingest, render, structure
from .agents import AnthropicClient, LLMError


# --------------------------------------------------------------------------
# Phase 0: ingest + structure (no LLM cost)
# --------------------------------------------------------------------------
def add_material(name, source, *, is_url=False, exam_name="", objectives="",
                 focus="", declared_type=None, log=print):
    db.init_db()
    mid = db.new_id()

    if is_url:
        log(f"Fetching {source} ...")
        text = ingest.extract_url(source)
        source_path, source_ref = "", source
        pdf_dest, pdf_chapters = None, None
    else:
        src = Path(source)
        if src.suffix.lower() == ".pdf":
            pdf_dest = config.UPLOADS_DIR / f"{mid}.pdf"
            shutil.copy(src, pdf_dest)
            source_path, source_ref = config.rel(pdf_dest), str(src)
            # Efficiency: check bookmarks first (cheap metadata read). Only fall
            # back to extracting every page when there's nothing to key off of.
            pdf_chapters = structure.detect_pdf_chapters(pdf_dest)
            if pdf_chapters:
                sample = ingest.extract_pdf_sample(pdf_dest)
                if not sample.strip():
                    raise ingest.ScannedPdfError(
                        f"'{name}' yielded no extractable text in its opening pages; "
                        "likely scanned. Run OCR (e.g. ocrmypdf) first, then re-add.")
                text = sample  # only needed for type inference below
            else:
                text = "\n".join(ingest.extract_pdf_pages(pdf_dest))  # raises on scanned
        else:
            text = ingest.extract_file(src)
            pdf_dest, source_path, source_ref, pdf_chapters = None, "", str(src), None

    mtype = structure.infer_material_type(name, text, declared_type)
    log(f"Material type: {mtype}")

    # persist the material row
    with db.conn() as c:
        c.execute("""INSERT INTO materials
            (id,name,material_type,exam_name,objectives,focus,source_path,source_ref)
            VALUES (?,?,?,?,?,?,?,?)""",
            (mid, name, mtype, exam_name, objectives, focus, source_path, source_ref))

    # build sections
    if pdf_chapters:
        for i, ch in enumerate(pdf_chapters, 1):
            db.add_section(mid, i, ch["title"],
                           start_page=ch["start_page"], end_page=ch["end_page"])
        log(f"Detected {len(pdf_chapters)} chapters from bookmarks.")
    else:
        _sections_from_text(mid, text, log)

    if not db.get_active():
        db.set_active(mid)
    log(f"Added '{name}' [{mid}].")
    return mid


def _sections_from_text(mid, text, log):
    sec_dir = config.EXTRACTED_DIR / mid
    sec_dir.mkdir(parents=True, exist_ok=True)
    sections = structure.segment_text(text)
    for i, (title, body) in enumerate(sections, 1):
        safe = "".join(ch if ch.isalnum() else "_" for ch in title)[:40] or "section"
        tp = sec_dir / f"{i:02d}_{safe}.txt"
        tp.write_text(body, encoding="utf-8")
        db.add_section(mid, i, title, text_path=config.rel(tp))
    log(f"Segmented into {len(sections)} section(s).")


def _section_source_text(material, section):
    if section.get("text_path"):
        return config.abspath(section["text_path"]).read_text(encoding="utf-8", errors="replace")
    return ingest.extract_pdf_range(
        config.abspath(material["source_path"]), section["start_page"], section["end_page"])


# --------------------------------------------------------------------------
# Phase 1: process (LLM cost) — the agent chain
# --------------------------------------------------------------------------
def process_material(mid, *, settings=None, make_learning=None, log=print, progress=None):
    settings = settings or config.load_settings()
    if make_learning is None:
        make_learning = settings.get("make_learning_artifacts", True)
    api_key = config.get_api_key(settings)
    if not api_key:
        raise RuntimeError(
            f"No AI provider configured. Open Settings to add your Anthropic API key, "
            f"or export {settings['anthropic_api_key_env']}.")
    client = AnthropicClient(api_key, settings["model"], settings["max_tokens"],
                             settings["max_retries"])

    material = db.get_material(mid)
    sections = db.get_sections(mid)
    out_dir = config.SUMMARIES_DIR / mid
    out_dir.mkdir(parents=True, exist_ok=True)
    pending = [s for s in sections if s["status"] != "generated"]
    log(f"Processing '{material['name']}': {len(pending)}/{len(sections)} section(s) pending.")

    for idx, sec in enumerate(pending, 1):
        if progress:
            progress(idx, len(pending), sec["title"])
        db.update_section(sec["id"], status="summarizing", error="")
        try:
            src = _section_source_text(material, sec)
            if not src.strip():
                raise LLMError("no extractable source text for this section")

            wc = len(src.split())
            # Agent 2: summarize (grounded)
            summary = summarize_agent_call(client, material, sec, src, wc, settings)

            # Agent 4: grounding check (optional)
            grounded, unsupported = 1, ""
            if settings.get("grounding_check", True):
                verdict = _safe_ground(client, summary, src, log, settings)
                grounded = 1 if verdict.get("grounded") else 0
                unsupported = "; ".join(verdict.get("unsupported_claims", [])[:5])

            # write outputs (respects Settings > Processing > Output format)
            out_fmt = settings.get("output_format", "both")
            slug = "".join(ch if ch.isalnum() else "_" for ch in sec["title"])[:50] or "section"
            md_path = out_dir / f"{sec['ordinal']:02d}_{slug}.md"
            pdf_path = out_dir / f"{sec['ordinal']:02d}_{slug}.pdf"
            md_rel = pdf_rel = ""
            if out_fmt in ("both", "md"):
                md_path.write_text(summary, encoding="utf-8")
                md_rel = config.rel(md_path)
            if out_fmt in ("both", "pdf"):
                render.render_pdf(summary, pdf_path, sec["title"])
                pdf_rel = config.rel(pdf_path)

            db.update_section(sec["id"], status="generated",
                              summary_md=md_rel, summary_pdf=pdf_rel,
                              grounded=grounded, unsupported=unsupported)

            # Agent 3: learning artifacts (best-effort; failure here is non-fatal)
            if make_learning:
                _safe_learning(client, sec, summary, out_dir, log, settings)

            flag = "" if grounded else "  [!] ungrounded claims flagged"
            log(f"  [{idx}/{len(pending)}] OK: {sec['title']}{flag}")
        except Exception as e:  # noqa: BLE001 - isolate per section
            db.update_section(sec["id"], status="failed", error=str(e)[:500])
            log(f"  [{idx}/{len(pending)}] FAILED: {sec['title']} -> {e}")

    return db.stats(mid)


_DEPTH_DELTA = {"brief": -1, "standard": 0, "thorough": 1}


def summarize_agent_call(client, material, sec, src, wc, settings=None):
    from .agents import summarize_agent
    settings = settings or {}
    depth = settings.get("summary_depth", "standard")
    pages = max(1, structure.target_pages(wc) + _DEPTH_DELTA.get(depth, 0))
    return summarize_agent(
        client, title=sec["title"], text=src, material_type=material["material_type"],
        target_pages=pages, exam_name=material["exam_name"],
        objectives=material["objectives"], focus=material["focus"],
        system_override=settings.get("agent2_summarize_prompt") or None)


def _safe_ground(client, summary, src, log, settings=None):
    from .agents import grounding_agent
    try:
        return grounding_agent(client, summary_md=summary, source_text=src,
                               system_override=(settings or {}).get("agent4_grounding_prompt") or None)
    except Exception as e:  # noqa: BLE001
        log(f"    grounding check skipped: {e}")
        return {"grounded": True, "unsupported_claims": [], "score": 1.0}


def learning_json_path(material_id, ordinal):
    """Matches exactly what _safe_learning() writes -- ordinal only, no title slug."""
    return config.SUMMARIES_DIR / material_id / f"{ordinal:02d}_learning.json"


def load_learning(material_id, ordinal):
    """Returns the parsed {"flashcards": [...], "quiz": [...]} dict, or None if missing/corrupt."""
    import json
    path = learning_json_path(material_id, ordinal)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _safe_learning(client, sec, summary, out_dir, log, settings=None):
    import json
    from .agents import learning_agent
    try:
        arts = learning_agent(client, title=sec["title"], summary_md=summary,
                              system_override=(settings or {}).get("agent3_learning_prompt") or None)
        (out_dir / f"{sec['ordinal']:02d}_learning.json").write_text(
            json.dumps(arts, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log(f"    learning artifacts skipped: {e}")


def process_failed_only(mid, **kw):
    """Re-run just the failed sections."""
    for s in db.get_sections(mid):
        if s["status"] == "failed":
            db.update_section(s["id"], status="pending")
    return process_material(mid, **kw)


# --------------------------------------------------------------------------
# Phase 2: deliver (cheap) — one section per run
# --------------------------------------------------------------------------
def send_next(mid=None, *, settings=None, log=print):
    settings = settings or config.load_settings()
    material = db.get_material(mid) if mid else db.get_active()
    if not material:
        log("No material to send.")
        return {"status": "no_material"}
    sec = db.next_undelivered(material["id"])
    if not sec:
        log("All caught up — nothing new to send.")
        return {"status": "caught_up"}

    if not sec.get("summary_pdf"):
        log("No PDF available for this section (output format set to Markdown-only in Settings).")
        return {"status": "error", "message": "No PDF to email — set Output format to PDF or Markdown+PDF in Settings."}

    pdf = config.abspath(sec["summary_pdf"])
    subject = (f"{material['exam_name']} summary: {sec['title']}"
               if material["exam_name"] else f"Summary: {sec['title']}")
    body = f'Attached: your summary of "{sec["title"]}" from {material["name"]}.'

    if settings.get("email_backend") == "none":
        return _save_local(material, sec, pdf, log, reason="email disabled")

    try:
        email_delivery.send(settings, subject, body, pdf)
        db.update_section(sec["id"], delivery="sent")
        log(f"Sent: {sec['title']}")
        return {"status": "sent", "section": sec["title"]}
    except Exception as e:  # noqa: BLE001
        log(f"Email FAILED: {e}")
        result = _save_local(material, sec, pdf, log, reason=str(e))
        if not settings.get("advance_queue_on_email_failure", False):
            # V2: leave delivery='none' so the queue does NOT silently advance
            db.update_section(sec["id"], delivery="none",
                              error=f"email failed, saved local: {str(e)[:300]}")
            result["queue_advanced"] = False
        return result


def _save_local(material, sec, pdf, log, reason=""):
    dest_dir = config.UNSENT_DIR / material["id"]
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(pdf).name
    shutil.copy(pdf, dest)
    db.update_section(sec["id"], delivery="delivered_local")
    log(f"Saved locally: {dest}  ({reason})")
    return {"status": "delivered_local", "path": str(dest), "reason": reason}


# --------------------------------------------------------------------------
# Interview prep (separate from the study-material pipeline)
# --------------------------------------------------------------------------
INTERVIEW_DIR = config.DATA_DIR / "interviews"


def add_interview_prep(role_title, *, company="", job_posting_source=None,
                       job_posting_text="", resume_source=None, resume_text="",
                       notes="", log=print):
    """Create an interview-prep record. Sources may be uploaded file paths (extracted to
    text) or pasted text. No LLM cost here -- generation is a separate step."""
    from . import ingest
    db.init_db()

    jp = job_posting_text
    if job_posting_source:
        jp = ingest.extract_file(job_posting_source)
    rt = resume_text
    if resume_source:
        rt = ingest.extract_file(resume_source)

    iid = db.add_interview_prep(role_title, company=company, job_posting=jp,
                                resume_text=rt, notes=notes)
    log(f"Added interview prep '{role_title}' [{iid}].")
    return iid


def generate_interview_prep(iid, *, settings=None, log=print):
    import json
    from . import interview, render

    settings = settings or config.load_settings()
    api_key = config.get_api_key(settings)
    if not api_key:
        raise RuntimeError(
            f"No AI provider configured. Open Settings to add your Anthropic API key, "
            f"or export {settings['anthropic_api_key_env']}.")

    prep = db.get_interview_prep(iid)
    if not prep:
        raise RuntimeError(f"No interview prep found with id {iid}")

    client = interview.AnthropicClient(api_key, settings["model"], settings["max_tokens"],
                                       settings["max_retries"])
    db.update_interview_prep(iid, status="generating", error="")
    try:
        kw = dict(role_title=prep["role_title"], company=prep["company"],
                  job_posting=prep["job_posting"], resume_text=prep["resume_text"],
                  notes=prep["notes"])
        log("Generating prep document...")
        prep_md = interview.generate_prep_markdown(client, **kw)

        log("Generating rehearsal questions...")
        try:
            questions = interview.generate_questions(client, **kw)
        except Exception as e:  # noqa: BLE001 -- questions are best-effort, doc still delivered
            log(f"  questions step skipped: {e}")
            questions = []

        INTERVIEW_DIR.mkdir(parents=True, exist_ok=True)
        slug = "".join(ch if ch.isalnum() else "_" for ch in prep["role_title"])[:50] or "prep"
        md_path = INTERVIEW_DIR / f"{iid}_{slug}.md"
        pdf_path = INTERVIEW_DIR / f"{iid}_{slug}.pdf"
        md_path.write_text(prep_md, encoding="utf-8")
        title = prep["role_title"] + (f" — {prep['company']}" if prep["company"] else "")
        render.render_pdf(prep_md, pdf_path, title)

        db.update_interview_prep(
            iid, status="generated", prep_md=config.rel(md_path),
            prep_pdf=config.rel(pdf_path), questions_json=json.dumps(questions))
        log(f"Done: {len(questions)} rehearsal questions, PDF written.")
        return {"status": "generated", "questions": len(questions)}
    except Exception as e:  # noqa: BLE001
        db.update_interview_prep(iid, status="failed", error=str(e)[:500])
        log(f"FAILED: {e}")
        raise


# --------------------------------------------------------------------------
# Language learning (stateful; separate from study-material + interview flows)
# --------------------------------------------------------------------------
LANG_DIR = config.DATA_DIR / "languages"


def _lang_client(settings):
    from . import language
    api_key = config.get_api_key(settings)
    if not api_key:
        raise RuntimeError(
            f"No AI provider configured. Open Settings to add your Anthropic API key, "
            f"or export {settings['anthropic_api_key_env']}.")
    return language.AnthropicClient(api_key, settings["model"], settings["max_tokens"],
                                    settings["max_retries"])


def add_lang_material(profile_id, kind, name, source_path, *, log=print):
    """Register a language material file (textbook/notes). Text extraction + curriculum
    analysis happens in analyze_lang_material (LLM step)."""
    dest_dir = LANG_DIR / profile_id / "materials"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(source_path).name
    shutil.copy(source_path, dest)
    mid = db.add_lang_material(profile_id, kind, name, config.rel(dest))
    log(f"Added language material '{name}' [{mid}]")
    return mid


def analyze_lang_material(material_id, *, settings=None, log=print):
    from . import ingest, language
    settings = settings or config.load_settings()
    client = _lang_client(settings)
    mat = db.get_lang_material(material_id)
    prof = db.get_lang_profile(mat["profile_id"])
    db.update_lang_material(material_id, status="pending", error="")
    try:
        path = config.abspath(mat["source_path"])
        ext = path.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            block = _image_block(path)
            data = language.extract_from_images(
                client, language=prof["language"], image_blocks=[block])
            curriculum = {"units": [{"title": "Extracted notes",
                                     "grammar": data.get("grammar", []),
                                     "vocabulary": data.get("vocabulary", []),
                                     "skills": data.get("concepts", [])}],
                          "progression_notes": data.get("uncertainty_notes", "")}
            needs_review = 1 if data.get("uncertain") else 0
        else:
            text = ingest.extract_file(path)
            if not text.strip():
                raise RuntimeError("No extractable text (scanned PDF? upload photos instead).")
            curriculum = language.extract_curriculum(
                client, language=prof["language"], material_text=text)
            needs_review = 0
        db.update_lang_material(material_id, status="analyzed", needs_review=needs_review,
                                curriculum_json=json.dumps(curriculum))
        log(f"Analyzed '{mat['name']}' — {len(curriculum.get('units', []))} unit(s)"
            + (" (needs review)" if needs_review else ""))
        return curriculum
    except Exception as e:  # noqa: BLE001
        db.update_lang_material(material_id, status="failed", error=str(e)[:400])
        log(f"FAILED analyzing '{mat['name']}': {e}")
        raise


def _image_block(path):
    import base64
    ext = path.suffix.lower().lstrip(".")
    media = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
             "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def run_lang_diagnostic(profile_id, *, settings=None, log=print):
    from . import language
    settings = settings or config.load_settings()
    client = _lang_client(settings)
    prof = db.get_lang_profile(profile_id)

    # Assessment data (evidence) = analyzed materials' concepts + any prior context.
    evidence_parts = []
    curricula = []
    for m in db.get_lang_materials(profile_id):
        if m["curriculum_json"]:
            cur = json.loads(m["curriculum_json"])
            curricula.append(cur)
            evidence_parts.append(f"Material '{m['name']}': {json.dumps(cur)[:1500]}")
    evidence_text = "\n".join(evidence_parts)

    log("Running diagnostic...")
    diag = language.diagnose(
        client, language=prof["language"], goal=prof["goal"], level_self=prof["level_self"],
        context=prof["context"], evidence_text=evidence_text)

    log("Building learning path...")
    merged_curriculum = curricula[0] if curricula else None
    path = language.build_path(client, language=prof["language"], goal=prof["goal"],
                               diagnostic=diag, curriculum=merged_curriculum)

    # Seed the learner model (concepts) from the current unit -> spaced review starts here.
    existing = {c["concept"] for c in db.get_lang_concepts(profile_id)}
    for c in path.get("current_unit", {}).get("concepts", []):
        if c.get("concept") and c["concept"] not in existing:
            db.add_lang_concept(profile_id, c["concept"], c.get("category", "grammar"),
                                status="learning", review_priority=1)

    db.update_lang_profile(
        profile_id, status="active", framework=diag.get("framework", ""),
        est_level=diag.get("est_level", ""), diagnostic_json=json.dumps(diag),
        path_json=json.dumps(path))
    log("Diagnostic complete.")
    return {"diagnostic": diag, "path": path}


def create_lang_assessment(profile_id, kind, *, settings=None, log=print):
    from . import language
    settings = settings or config.load_settings()
    client = _lang_client(settings)
    prof = db.get_lang_profile(profile_id)
    weaknesses = []
    if prof.get("diagnostic_json"):
        try:
            weaknesses = json.loads(prof["diagnostic_json"]).get("priorities", [])
        except json.JSONDecodeError:
            pass
    prompts = language.generate_prompts(
        client, language=prof["language"], goal=prof["goal"],
        level=prof.get("est_level") or prof.get("level_self") or "beginner",
        kind=kind, weaknesses=weaknesses, n=8)
    audio_dir = ""
    if kind == "speaking":
        aid_dir = LANG_DIR / profile_id / "assessments"
        aid_dir.mkdir(parents=True, exist_ok=True)
        audio_dir = config.rel(aid_dir)
    aid = db.add_lang_assessment(profile_id, kind, json.dumps(prompts), audio_dir)
    log(f"Created {kind} assessment with {len(prompts)} prompts")
    return aid


def score_lang_assessment(assessment_id, responses, *, audio_based=False, settings=None, log=print):
    """responses: list of {prompt, text}. Scores via the reusable engine and stores history."""
    from . import language
    settings = settings or config.load_settings()
    client = _lang_client(settings)
    a = db.get_lang_assessment(assessment_id)
    prof = db.get_lang_profile(a["profile_id"])
    db.update_lang_assessment(assessment_id, status="submitted",
                              responses_json=json.dumps(responses))
    try:
        result = language.score_production(
            client, language=prof["language"],
            level=prof.get("est_level") or "beginner",
            responses=responses, audio_based=audio_based)
        s = result.get("scores", {})
        db.update_lang_assessment(
            assessment_id, status="scored", audio_based=1 if audio_based else 0,
            score_fluency=s.get("fluency"), score_lexical=s.get("lexical"),
            score_grammar=s.get("grammar"), score_pronunciation=s.get("pronunciation"),
            score_overall=s.get("overall"),
            feedback_json=json.dumps(result.get("per_response", [])),
            recurring_json=json.dumps(result.get("recurring_errors", [])))
        # Feed recurring errors back into the learner model as review concepts.
        for err in result.get("recurring_errors", [])[:5]:
            db.add_lang_concept(a["profile_id"], err, "grammar",
                                status="review", review_priority=1)
        log(f"Scored: overall {s.get('overall')}")
        return result
    except Exception as e:  # noqa: BLE001
        db.update_lang_assessment(assessment_id, status="failed", error=str(e)[:400])
        log(f"Scoring FAILED: {e}")
        raise


def export_lang_plan_pdf(profile_id, *, log=print):
    from . import render
    prof = db.get_lang_profile(profile_id)
    path = json.loads(prof["path_json"]) if prof.get("path_json") else {}
    diag = json.loads(prof["diagnostic_json"]) if prof.get("diagnostic_json") else {}
    md = _lang_plan_markdown(prof, diag, path)
    LANG_DIR.joinpath(profile_id).mkdir(parents=True, exist_ok=True)
    pdf_path = LANG_DIR / profile_id / "learning_plan.pdf"
    render.render_pdf(md, pdf_path, f"{prof['language']} Learning Plan")
    db.update_lang_profile(profile_id, path_pdf=config.rel(pdf_path))
    log("Exported learning plan PDF")
    return pdf_path


def _lang_plan_markdown(prof, diag, path):
    lines = [f"# {prof['language']} Learning Plan", ""]
    lines += [f"**Goal:** {prof.get('goal','')}  ",
              f"**StudyForge estimated level:** {prof.get('est_level','')} "
              f"({prof.get('framework','')}) — *estimate, not an official test result*", ""]
    if diag.get("skills"):
        lines += ["## Current Skill Estimates", ""]
        for k, v in diag["skills"].items():
            lines.append(f"- {k.title()}: {v}/100")
        lines.append("")
    if path.get("immediate_priorities"):
        lines += ["## Immediate Priorities", ""]
        lines += [f"- {p}" for p in path["immediate_priorities"]] + [""]
    cu = path.get("current_unit", {})
    if cu:
        lines += [f"## Current Unit: {cu.get('title','')}", ""]
        lines += [f"- {c.get('concept','')} ({c.get('category','')})"
                  for c in cu.get("concepts", [])] + [""]
    if path.get("upcoming_units"):
        lines += ["## Upcoming Units", ""] + [f"- {u}" for u in path["upcoming_units"]] + [""]
    if path.get("weekly_plan"):
        lines += ["## Weekly Plan", ""] + [f"- {w}" for w in path["weekly_plan"]] + [""]
    if path.get("review_focus"):
        lines += ["## Review Focus", ""] + [f"- {r}" for r in path["review_focus"]] + [""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Case studies (nested under Interview Prep)
# --------------------------------------------------------------------------
CASE_DIR = config.DATA_DIR / "case_studies"

_DIFFICULTY_LABELS = {"easier": "Easier", "medium": "Medium", "harder": "Harder"}


def suggest_case_types(interview_prep_id, *, settings=None, log=print):
    from . import case_study
    settings = settings or config.load_settings()
    api_key = config.get_api_key(settings)
    if not api_key:
        raise RuntimeError(
            f"No AI provider configured. Open Settings to add your Anthropic API key, "
            f"or export {settings['anthropic_api_key_env']}.")
    prep = db.get_interview_prep(interview_prep_id)
    if not prep:
        raise RuntimeError("Unknown interview prep.")
    client = case_study.AnthropicClient(api_key, settings["model"], settings["max_tokens"],
                                        settings["max_retries"])
    return case_study.suggest_case_types(
        client, role_title=prep["role_title"], company=prep["company"],
        job_posting=prep["job_posting"])


def create_and_generate_case_study(interview_prep_id, *, source_prep_id=None,
                                   case_type="", case_type_label="", difficulty="medium",
                                   settings=None, log=print):
    from . import case_study, render
    settings = settings or config.load_settings()
    api_key = config.get_api_key(settings)
    if not api_key:
        raise RuntimeError(
            f"No AI provider configured. Open Settings to add your Anthropic API key, "
            f"or export {settings['anthropic_api_key_env']}.")

    source_prep_id = source_prep_id or interview_prep_id
    source = db.get_interview_prep(source_prep_id)
    if not source:
        raise RuntimeError("Unknown source interview prep for the job description.")
    if not source["job_posting"].strip():
        raise RuntimeError("The selected interview prep has no job posting text to base a case on.")

    cid = db.add_case_study(interview_prep_id, source_prep_id, case_type, case_type_label,
                            difficulty)
    client = case_study.AnthropicClient(api_key, settings["model"], settings["max_tokens"],
                                        settings["max_retries"])
    db.update_case_study(cid, status="generating")
    try:
        log(f"Generating {case_type_label} case study ({difficulty})...")
        data = case_study.generate_case_study(
            client, role_title=source["role_title"], company=source["company"],
            job_posting=source["job_posting"], resume_text=source["resume_text"],
            case_type_label=case_type_label, difficulty=_DIFFICULTY_LABELS.get(difficulty, difficulty))

        title = data.get("title") or f"{case_type_label} Case Study"
        questions = data.get("questions", [])
        solutions = data.get("solutions", [])

        CASE_DIR.mkdir(parents=True, exist_ok=True)
        slug = "".join(ch if ch.isalnum() else "_" for ch in title)[:50] or "case"
        pdf_path = CASE_DIR / f"{cid}_{slug}.pdf"
        pdf_md = _case_pdf_markdown(title, data.get("scenario_md", ""), solutions, questions)
        render.render_pdf(pdf_md, pdf_path, title)

        db.update_case_study(
            cid, status="generated", title=title, scenario_md=data.get("scenario_md", ""),
            questions_json=json.dumps(questions), solutions_json=json.dumps(solutions),
            case_pdf=config.rel(pdf_path))
        log(f"Case study generated: {len(questions)} question(s).")
        return cid
    except Exception as e:  # noqa: BLE001
        db.update_case_study(cid, status="failed", error=str(e)[:400])
        log(f"FAILED: {e}")
        raise


def _case_pdf_markdown(title, scenario_md, solutions, questions):
    q_lookup = {q["id"]: q["question"] for q in questions}
    parts = [scenario_md.strip(), "", "<!--pagebreak-->", "", "# Potential Ways to Answer", "",
            "*These are a few valid approaches — a strong answer doesn't need to match one "
            "exactly, but should cover the substance of at least one.*", ""]
    for sol in solutions:
        qid = sol.get("question_id", "")
        parts.append(f"## {q_lookup.get(qid, qid)}")
        parts.append("")
        for approach in sol.get("approaches", []):
            parts.append(f"### Approach: {approach.get('name','')}")
            for kp in approach.get("key_points", []):
                parts.append(f"- {kp}")
            parts.append("")
        if sol.get("model_notes"):
            parts.append(f"**Notes:** {sol['model_notes']}")
            parts.append("")
    return "\n".join(parts)


def start_case_attempt(case_study_id, duration_minutes=None, *, log=print):
    if duration_minutes is not None and int(duration_minutes) not in (20, 30, 60):
        raise RuntimeError("duration_minutes must be 20, 30, 60, or omitted for untimed practice.")
    aid = db.add_case_attempt(case_study_id, duration_minutes)
    log(f"Started attempt {aid} ({duration_minutes or 'untimed'} min)")
    return aid


def submit_case_attempt(attempt_id, responses, *, settings=None, log=print):
    """responses: list of {"question_id": str, "answer": str}."""
    from . import case_study
    settings = settings or config.load_settings()
    attempt = db.get_case_attempt(attempt_id)
    if not attempt:
        raise RuntimeError("Unknown attempt.")
    cs = db.get_case_study(attempt["case_study_id"])
    source = db.get_interview_prep(cs["source_prep_id"])

    db.update_case_attempt(attempt_id, status="submitted", submitted_at=_now_iso(),
                           responses_json=json.dumps(responses))

    api_key = config.get_api_key(settings)
    if not api_key:
        db.update_case_attempt(attempt_id, status="failed",
                               error="No AI provider configured.")
        raise RuntimeError("No AI provider configured. Open Settings to add your API key.")

    client = case_study.AnthropicClient(api_key, settings["model"], settings["max_tokens"],
                                        settings["max_retries"])
    solutions = json.loads(cs["solutions_json"]) if cs["solutions_json"] else []
    questions = {q["id"]: q["question"] for q in json.loads(cs["questions_json"] or "[]")}
    qws = [{"question_id": s["question_id"], "question_text": questions.get(s["question_id"], ""),
           "approaches": s.get("approaches", [])} for s in solutions]
    responses_by_qid = {r["question_id"]: r.get("answer", "") for r in responses}

    try:
        result = case_study.score_case_attempt(
            client, role_title=source["role_title"], job_posting=source["job_posting"],
            resume_text=source["resume_text"], questions_with_solutions=qws,
            responses_by_qid=responses_by_qid)
        db.update_case_attempt(
            attempt_id, status="scored",
            feedback_json=json.dumps(result.get("per_question", [])),
            gaps_json=json.dumps(result.get("gaps", [])),
            overall_summary=result.get("overall_summary", ""),
            score_percent=result.get("score_percent"))
        log(f"Scored: {result.get('score_percent')}%")
        return result
    except Exception as e:  # noqa: BLE001
        db.update_case_attempt(attempt_id, status="failed", error=str(e)[:400])
        log(f"Scoring FAILED: {e}")
        raise


def _now_iso():
    from datetime import datetime
    return datetime.now().isoformat()


# --------------------------------------------------------------------------
# Offer comparison (nested under Interview Prep)
# --------------------------------------------------------------------------
def _offers_client(settings):
    from . import offers as offers_mod
    api_key = config.get_api_key(settings)
    if not api_key:
        raise RuntimeError(
            f"No AI provider configured. Open Settings to add your Anthropic API key, "
            f"or export {settings['anthropic_api_key_env']}.")
    return offers_mod.AnthropicClient(api_key, settings["model"], settings["max_tokens"],
                                      settings["max_retries"])


def add_offer_to_comparison(comparison_id, label, *, job_posting_source=None,
                            job_posting_text="", **kw):
    """Add one offer. The JD may be pasted text or an uploaded file (extracted here)."""
    from . import ingest
    jp = job_posting_text
    if job_posting_source:
        jp = ingest.extract_file(job_posting_source)
    return db.add_offer(comparison_id, label, job_posting=jp, **kw)


def generate_offer_questions(comparison_id, *, settings=None, log=print):
    from . import offers as offers_mod
    settings = settings or config.load_settings()
    comp = db.get_offer_comparison(comparison_id)
    if not comp:
        raise RuntimeError("Unknown comparison.")
    offer_rows = db.get_offers(comparison_id)
    if len(offer_rows) < 2:
        raise RuntimeError("Add at least two offers before generating questions.")
    client = _offers_client(settings)
    try:
        questions = offers_mod.generate_questions(
            client, offers=offer_rows, resume_text=comp["resume_text"],
            priorities=comp["priorities"])
        if not questions:
            raise RuntimeError("No questions were generated. Try again.")
        db.update_offer_comparison(comparison_id, status="questioned", error="",
                                   questions_json=json.dumps(questions))
        log(f"Generated {len(questions)} questions")
        return questions
    except Exception as e:  # noqa: BLE001
        db.update_offer_comparison(comparison_id, status="failed", error=str(e)[:400])
        log(f"FAILED: {e}")
        raise


def rank_offer_comparison(comparison_id, answers_by_id, *, settings=None, log=print):
    from . import offers as offers_mod
    settings = settings or config.load_settings()
    comp = db.get_offer_comparison(comparison_id)
    if not comp:
        raise RuntimeError("Unknown comparison.")
    questions = json.loads(comp["questions_json"]) if comp["questions_json"] else []
    if not questions:
        raise RuntimeError("Generate the questions before ranking.")
    offer_rows = db.get_offers(comparison_id)
    db.update_offer_comparison(comparison_id, answers_json=json.dumps(answers_by_id))
    client = _offers_client(settings)
    try:
        result = offers_mod.rank_offers(
            client, offers=offer_rows, resume_text=comp["resume_text"],
            priorities=comp["priorities"], questions=questions, answers_by_id=answers_by_id)
        db.update_offer_comparison(
            comparison_id, status="ranked", error="",
            ranking_json=json.dumps(result.get("ranking", [])),
            summary=result.get("summary", ""))
        log("Ranked offers")
        return result
    except Exception as e:  # noqa: BLE001
        db.update_offer_comparison(comparison_id, status="failed", error=str(e)[:400])
        log(f"FAILED: {e}")
        raise

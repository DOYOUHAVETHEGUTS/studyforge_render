"""
FastAPI web dashboard.

Serves a live, data-driven version of the UI (same look as the prototype) plus
JSON/trigger endpoints so you can run real test jobs from the browser. Processing
runs in a background thread so the request returns immediately; poll /api/material/{id}
for status.

Run:  python -m studyforge.cli serve   (or)   uvicorn studyforge.web:app
"""
import asyncio
import json
import shutil
import threading
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from . import config, db, email_delivery, managed_agents, pipeline, scheduler

app = FastAPI(title="StudyForge")
BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

_jobs = {}  # material_id -> {"log": [...], "running": bool}

# ---- optional shared-password gate --------------------------------------
# When STUDYFORGE_ACCESS_PASSWORD is set (deployment), every request must carry a
# valid session cookie obtained via /login. When it's unset (local use), the gate is
# fully disabled and behavior is unchanged.
import hashlib
import hmac
import os

_ACCESS_PASSWORD = os.environ.get("STUDYFORGE_ACCESS_PASSWORD", "")
# Cookie value is an HMAC of a constant over the password, so it can't be forged
# without knowing the password, and doesn't expose the password itself.
_COOKIE_NAME = "sf_auth"


def _expected_cookie():
    return hmac.new(_ACCESS_PASSWORD.encode(), b"studyforge-access", hashlib.sha256).hexdigest()


_OPEN_PATHS = ("/login", "/static", "/favicon.ico")


@app.middleware("http")
async def _auth_gate(request: Request, call_next):
    if _ACCESS_PASSWORD and not request.url.path.startswith(_OPEN_PATHS):
        if request.cookies.get(_COOKIE_NAME) != _expected_cookie():
            if request.url.path.startswith("/api/") or request.method != "GET":
                return JSONResponse({"status": "error", "message": "Not authorized."},
                                    status_code=401)
            return RedirectResponse("/login", status_code=303)
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, bad: bool = False):
    if not _ACCESS_PASSWORD:
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "login.html", {"bad": bad})


@app.post("/login")
async def login_submit(request: Request):
    form = dict((await request.form()))
    if _ACCESS_PASSWORD and hmac.compare_digest(form.get("password", ""), _ACCESS_PASSWORD):
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(_COOKIE_NAME, _expected_cookie(), httponly=True, samesite="lax",
                        max_age=60 * 60 * 24 * 30)
        return resp
    return RedirectResponse("/login?bad=true", status_code=303)


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(_COOKIE_NAME)
    return resp


_scheduler_log = []


@app.on_event("startup")
async def _startup():
    db.init_db()
    asyncio.create_task(scheduler.run_scheduler(log=lambda m: _scheduler_log.append(m)))


def _run_job(mid, failed_only=False):
    _jobs[mid] = {"log": [], "running": True}

    def log(msg):
        _jobs[mid]["log"].append(str(msg))

    try:
        fn = pipeline.process_failed_only if failed_only else pipeline.process_material
        fn(mid, log=log)
    except Exception as e:  # noqa: BLE001
        log(f"FATAL: {e}")
    finally:
        _jobs[mid]["running"] = False


# ---- pages ---------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    mats = []
    for m in db.list_materials():
        m = dict(m)
        m["stats"] = db.stats(m["id"])
        mats.append(m)
    settings = config.load_settings()
    return templates.TemplateResponse(request, "index.html", {
        "materials": mats, "ready": config.readiness(settings),
        "display_name": settings.get("account_name", ""),
        "supported_types": ".pdf, .docx, .txt, .md",
    })


@app.get("/book/{mid}", response_class=HTMLResponse)
def book(request: Request, mid: str):
    material = db.get_material(mid)
    if not material:
        return RedirectResponse("/")
    sections = db.get_sections(mid)
    for s in sections:
        s["has_learning"] = pipeline.learning_json_path(mid, s["ordinal"]).exists()
    return templates.TemplateResponse(request, "book.html", {
        "m": material,
        "sections": sections, "stats": db.stats(mid),
        "job": _jobs.get(mid),
    })


@app.get("/section/{sid}/study", response_class=HTMLResponse)
def section_study(request: Request, sid: str):
    sec = db.get_section(sid)
    if not sec:
        return RedirectResponse("/")
    material = db.get_material(sec["material_id"])
    learning = pipeline.load_learning(sec["material_id"], sec["ordinal"])
    return templates.TemplateResponse(request, "study.html", {
        "sec": sec, "m": material, "learning": learning,
    })


@app.post("/section/{sid}/quiz-result")
async def quiz_result(sid: str, request: Request):
    body = await request.json()
    correct, total = int(body.get("correct", 0)), int(body.get("total", 0))
    db.record_quiz_result(sid, correct, total)
    sec = db.get_section(sid)
    return JSONResponse({"status": "recorded", "best_score": sec.get("quiz_best_score"),
                         "attempts": sec.get("quiz_attempts")})


@app.get("/prototype", response_class=HTMLResponse)
def prototype():
    return (BASE / "static" / "prototype.html").read_text(encoding="utf-8")


@app.get("/section/{sid}/pdf")
def section_pdf(sid: str):
    sec = db.get_section(sid)
    if not sec or not sec.get("summary_pdf"):
        return JSONResponse(
            {"status": "error",
             "message": "No PDF for this section yet — it may not be processed, or "
                        "Settings > Processing > Output format is set to Markdown-only."},
            status_code=404)
    path = config.abspath(sec["summary_pdf"])
    if not path.exists():
        return JSONResponse({"status": "error", "message": "PDF file is missing on disk."},
                            status_code=404)
    # Explicit inline disposition (not just an omitted header) so the browser's built-in
    # PDF viewer renders it in the tab, with a sensible filename if the user does Save As.
    return FileResponse(path, media_type="application/pdf", filename=path.name,
                        content_disposition_type="inline")


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: bool = False, synced: int = 0, sync_error: str = ""):
    settings = config.load_settings()
    return templates.TemplateResponse(request, "settings.html", {
        "s": config.settings_for_display(settings), "ready": config.readiness(settings),
        "saved": saved, "synced": synced, "sync_error": sync_error,
    })


@app.post("/settings")
async def save_settings(request: Request):
    form = dict((await request.form()))
    current = config.load_settings()
    updated = config.apply_settings_update(current, form)
    config.save_settings(updated)
    config.update_secrets_from_form(form)
    return RedirectResponse("/settings?saved=true", status_code=303)


_AGENT_FIELD_MAP = {
    1: ("agent1_structure_id", "agent1_structure_prompt"),
    2: ("agent2_summarize_id", "agent2_summarize_prompt"),
    3: ("agent3_learning_id", "agent3_learning_prompt"),
    4: ("agent4_grounding_id", "agent4_grounding_prompt"),
}


@app.post("/settings/sync-agent/{n}")
async def sync_agent(n: int, request: Request):
    if n not in _AGENT_FIELD_MAP:
        return RedirectResponse("/settings?sync_error=Unknown+agent+number", status_code=303)
    id_field, prompt_field = _AGENT_FIELD_MAP[n]

    # The Sync button submits the whole form (via formaction) — save it first so a
    # freshly-typed agent ID (not yet clicked "Save settings") is what actually gets used.
    form = dict((await request.form()))
    current = config.load_settings()
    updated = config.apply_settings_update(current, form)
    config.save_settings(updated)
    config.update_secrets_from_form(form)

    settings = config.load_settings()
    agent_id = settings.get(id_field, "")
    api_key = config.get_api_key(settings)
    try:
        prompt_text = managed_agents.sync_agent_id(agent_id, api_key)
        settings[prompt_field] = prompt_text
        config.save_settings(settings)
        return RedirectResponse(f"/settings?synced={n}", status_code=303)
    except managed_agents.ManagedAgentsError as e:
        from urllib.parse import quote
        return RedirectResponse(f"/settings?sync_error={quote(str(e)[:200])}", status_code=303)


# ---- material upload -------------------------------------------------------
@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    name: str = Form(""),
    material_type: str = Form(""),
    exam_name: str = Form(""),
    focus: str = Form(""),
):
    display_name = name.strip() or Path(file.filename).stem
    tmp_path = config.UPLOADS_DIR / f"_incoming_{file.filename}"
    try:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        mid = pipeline.add_material(
            display_name, str(tmp_path), exam_name=exam_name, focus=focus,
            declared_type=material_type or None, log=lambda *_: None,
        )
        return JSONResponse({"status": "added", "material_id": mid})
    except Exception as e:  # noqa: BLE001 — surface a clear message, not a 500 traceback
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    finally:
        tmp_path.unlink(missing_ok=True)


# ---- actions -------------------------------------------------------------
@app.post("/process/{mid}")
def process(mid: str, background: BackgroundTasks, failed_only: bool = False):
    if _jobs.get(mid, {}).get("running"):
        return JSONResponse({"status": "already_running"})
    settings = config.load_settings()
    if not config.readiness(settings)["ai"]:
        return JSONResponse({
            "status": "error",
            "message": "An AI provider has not been configured yet. Open Settings to add your API key.",
        }, status_code=400)
    background.add_task(_run_job, mid, failed_only)
    return JSONResponse({"status": "started"})


@app.post("/send/{mid}")
def send(mid: str):
    return JSONResponse(pipeline.send_next(mid))


@app.post("/active/{mid}")
def active(mid: str):
    db.set_active(mid)
    return RedirectResponse("/", status_code=303)


@app.post("/delete/{mid}")
def delete(mid: str):
    db.delete_material(mid)
    return RedirectResponse("/", status_code=303)


# ---- language learning ---------------------------------------------------
_lang_jobs = {}  # profile_id -> {"log": [...], "running": bool}


@app.get("/languages", response_class=HTMLResponse)
def lang_list(request: Request):
    return templates.TemplateResponse(request, "lang_list.html", {
        "profiles": db.list_lang_profiles(), "ready": config.readiness(config.load_settings()),
    })


@app.post("/languages/create")
async def lang_create(
    language: str = Form(...),
    goal: str = Form(""),
    frequency: str = Form(""),
    target_date: str = Form(""),
    level_self: str = Form(""),
    context: str = Form(""),
):
    if not language.strip():
        return JSONResponse({"status": "error", "message": "Pick a language."}, status_code=400)
    pid = db.add_lang_profile(language.strip(), goal=goal, frequency=frequency,
                              target_date=target_date, level_self=level_self, context=context)
    return JSONResponse({"status": "added", "profile_id": pid})


@app.post("/languages/{pid}/material")
async def lang_material(pid: str, background: BackgroundTasks,
                        kind: str = Form("textbook"), file: UploadFile = File(...)):
    if not db.get_lang_profile(pid):
        return JSONResponse({"status": "error", "message": "Unknown profile."}, status_code=404)
    tmp = config.UPLOADS_DIR / f"_lang_{file.filename}"
    try:
        with open(tmp, "wb") as f:
            shutil.copyfileobj(file.file, f)
        mid = pipeline.add_lang_material(pid, kind, Path(file.filename).stem, str(tmp),
                                         log=lambda *_: None)
        settings = config.load_settings()
        if config.readiness(settings)["ai"]:
            background.add_task(_analyze_lang_material_job, mid)
            return JSONResponse({"status": "added", "material_id": mid, "analyzing": True})
        return JSONResponse({"status": "added", "material_id": mid, "analyzing": False})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    finally:
        tmp.unlink(missing_ok=True)


def _analyze_lang_material_job(mid):
    try:
        pipeline.analyze_lang_material(mid, log=lambda *_: None)
    except Exception:  # noqa: BLE001 - status/error already persisted on the row
        pass


def _run_lang_diagnostic_job(pid):
    _lang_jobs[pid] = {"log": [], "running": True}
    log = lambda m: _lang_jobs[pid]["log"].append(str(m))  # noqa: E731
    try:
        pipeline.run_lang_diagnostic(pid, log=log)
    except Exception as e:  # noqa: BLE001
        log(f"FATAL: {e}")
    finally:
        _lang_jobs[pid]["running"] = False


@app.post("/languages/{pid}/diagnose")
def lang_diagnose(pid: str, background: BackgroundTasks):
    if _lang_jobs.get(pid, {}).get("running"):
        return JSONResponse({"status": "already_running"})
    if not config.readiness(config.load_settings())["ai"]:
        return JSONResponse({"status": "error",
                             "message": "An AI provider has not been configured yet. Open Settings."},
                            status_code=400)
    background.add_task(_run_lang_diagnostic_job, pid)
    return JSONResponse({"status": "started"})


@app.get("/languages/{pid}", response_class=HTMLResponse)
def lang_dashboard(request: Request, pid: str):
    prof = db.get_lang_profile(pid)
    if not prof:
        return RedirectResponse("/languages")
    diag = json.loads(prof["diagnostic_json"]) if prof.get("diagnostic_json") else {}
    path = json.loads(prof["path_json"]) if prof.get("path_json") else {}
    return templates.TemplateResponse(request, "lang_dashboard.html", {
        "p": prof, "diag": diag, "path": path,
        "materials": db.get_lang_materials(pid),
        "concepts": db.get_lang_concepts(pid),
        "assessments": db.get_lang_assessments(pid),
        "job": _lang_jobs.get(pid),
    })


@app.post("/languages/{pid}/assessment/{kind}")
def lang_create_assessment(pid: str, kind: str):
    if kind not in ("speaking", "writing"):
        return JSONResponse({"status": "error", "message": "Bad kind."}, status_code=400)
    if not config.readiness(config.load_settings())["ai"]:
        return JSONResponse({"status": "error", "message": "Configure an AI provider in Settings."},
                            status_code=400)
    try:
        aid = pipeline.create_lang_assessment(pid, kind, log=lambda *_: None)
        return JSONResponse({"status": "created", "assessment_id": aid})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/languages/assessment/{aid}", response_class=HTMLResponse)
def lang_assessment_page(request: Request, aid: str):
    a = db.get_lang_assessment(aid)
    if not a:
        return RedirectResponse("/languages")
    prompts = json.loads(a["prompts_json"]) if a.get("prompts_json") else []
    feedback = json.loads(a["feedback_json"]) if a.get("feedback_json") else []
    recurring = json.loads(a["recurring_json"]) if a.get("recurring_json") else []
    responses = json.loads(a["responses_json"]) if a.get("responses_json") else []
    return templates.TemplateResponse(request, "lang_assessment.html", {
        "a": a, "prompts": prompts, "feedback": feedback, "recurring": recurring,
        "responses": responses, "p": db.get_lang_profile(a["profile_id"]),
    })


@app.post("/languages/assessment/{aid}/audio/{idx}")
async def lang_upload_audio(aid: str, idx: int, file: UploadFile = File(...)):
    """Store one recording for prompt `idx`. Audio is kept under data/ (not in static/),
    served only through the authenticated-by-obscurity app route below."""
    a = db.get_lang_assessment(aid)
    if not a:
        return JSONResponse({"status": "error", "message": "Unknown assessment."}, status_code=404)
    audio_dir = config.abspath(a["audio_dir"]) if a["audio_dir"] else (
        pipeline.LANG_DIR / a["profile_id"] / "assessments")
    audio_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "rec.webm").suffix or ".webm"
    dest = audio_dir / f"{aid}_{idx}{ext}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return JSONResponse({"status": "stored", "index": idx})


@app.get("/languages/assessment/{aid}/audio/{idx}")
def lang_get_audio(aid: str, idx: int):
    a = db.get_lang_assessment(aid)
    if not a or not a["audio_dir"]:
        return JSONResponse({"status": "error"}, status_code=404)
    audio_dir = config.abspath(a["audio_dir"])
    matches = list(audio_dir.glob(f"{aid}_{idx}.*")) if audio_dir.exists() else []
    if not matches:
        return JSONResponse({"status": "error", "message": "No recording."}, status_code=404)
    return FileResponse(matches[0])


@app.post("/languages/assessment/{aid}/submit")
async def lang_submit_assessment(aid: str, request: Request):
    """Body: {responses: [{prompt, text}], audio_based: bool}. Scores via the engine."""
    body = await request.json()
    responses = body.get("responses", [])
    audio_based = bool(body.get("audio_based", False))
    if not any((r.get("text") or "").strip() for r in responses):
        return JSONResponse(
            {"status": "error",
             "message": "No responses to score. Provide a transcript or written text for at "
                        "least one prompt."}, status_code=400)
    try:
        result = pipeline.score_lang_assessment(aid, responses, audio_based=audio_based,
                                                log=lambda *_: None)
        return JSONResponse({"status": "scored", "scores": result.get("scores", {})})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/languages/{pid}/plan.pdf")
def lang_plan_pdf(pid: str):
    prof = db.get_lang_profile(pid)
    if not prof or not prof.get("path_json"):
        return JSONResponse({"status": "error", "message": "Generate a learning path first."},
                            status_code=404)
    try:
        pdf = pipeline.export_lang_plan_pdf(pid, log=lambda *_: None)
        return FileResponse(pdf, media_type="application/pdf", filename=pdf.name,
                            content_disposition_type="inline")
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)


@app.get("/api/language/{pid}")
def api_language(pid: str):
    return {"profile": db.get_lang_profile(pid), "job": _lang_jobs.get(pid),
            "materials": db.get_lang_materials(pid)}


@app.post("/languages/{pid}/delete")
def lang_delete(pid: str):
    db.delete_lang_profile(pid)
    return RedirectResponse("/languages", status_code=303)


# ---- interview prep ------------------------------------------------------
_interview_jobs = {}  # iid -> {"log": [...], "running": bool}


@app.get("/interview", response_class=HTMLResponse)
def interview_list(request: Request):
    settings = config.load_settings()
    return templates.TemplateResponse(request, "interview_list.html", {
        "preps": db.list_interview_preps(),
        "ready": config.readiness(settings),
    })


@app.post("/interview/create")
async def interview_create(
    role_title: str = Form(...),
    company: str = Form(""),
    job_posting_text: str = Form(""),
    resume_text: str = Form(""),
    notes: str = Form(""),
    job_file: UploadFile = File(None),
    resume_file: UploadFile = File(None),
):
    if not role_title.strip():
        return JSONResponse({"status": "error", "message": "Role title is required."},
                            status_code=400)
    tmp_paths = []

    def _stash(upload):
        if upload is None or not upload.filename:
            return None
        p = config.UPLOADS_DIR / f"_iv_{upload.filename}"
        with open(p, "wb") as f:
            shutil.copyfileobj(upload.file, f)
        tmp_paths.append(p)
        return str(p)

    try:
        job_src = _stash(job_file)
        resume_src = _stash(resume_file)
        if not (job_src or job_posting_text.strip()):
            return JSONResponse(
                {"status": "error", "message": "Provide a job posting — paste text or upload a file."},
                status_code=400)
        iid = pipeline.add_interview_prep(
            role_title.strip(), company=company.strip(),
            job_posting_source=job_src, job_posting_text=job_posting_text,
            resume_source=resume_src, resume_text=resume_text,
            notes=notes, log=lambda *_: None,
        )
        return JSONResponse({"status": "added", "interview_id": iid})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "error", "message": str(e)}, status_code=400)
    finally:
        for p in tmp_paths:
            p.unlink(missing_ok=True)


def _run_interview_job(iid):
    _interview_jobs[iid] = {"log": [], "running": True}
    log = lambda m: _interview_jobs[iid]["log"].append(str(m))  # noqa: E731
    try:
        pipeline.generate_interview_prep(iid, log=log)
    except Exception as e:  # noqa: BLE001
        log(f"FATAL: {e}")
    finally:
        _interview_jobs[iid]["running"] = False


@app.post("/interview/{iid}/generate")
def interview_generate(iid: str, background: BackgroundTasks):
    if _interview_jobs.get(iid, {}).get("running"):
        return JSONResponse({"status": "already_running"})
    settings = config.load_settings()
    if not config.readiness(settings)["ai"]:
        return JSONResponse({
            "status": "error",
            "message": "An AI provider has not been configured yet. Open Settings to add your API key.",
        }, status_code=400)
    background.add_task(_run_interview_job, iid)
    return JSONResponse({"status": "started"})


@app.get("/interview/{iid}", response_class=HTMLResponse)
def interview_detail(request: Request, iid: str):
    import json
    prep = db.get_interview_prep(iid)
    if not prep:
        return RedirectResponse("/interview")
    questions = []
    if prep.get("questions_json"):
        try:
            questions = json.loads(prep["questions_json"])
        except json.JSONDecodeError:
            questions = []
    return templates.TemplateResponse(request, "interview_detail.html", {
        "p": prep, "questions": questions, "job": _interview_jobs.get(iid),
    })


@app.get("/interview/{iid}/pdf")
def interview_pdf(iid: str):
    prep = db.get_interview_prep(iid)
    if not prep or not prep.get("prep_pdf"):
        return JSONResponse({"status": "error", "message": "No PDF generated yet."},
                            status_code=404)
    path = config.abspath(prep["prep_pdf"])
    if not path.exists():
        return JSONResponse({"status": "error", "message": "PDF file is missing on disk."},
                            status_code=404)
    return FileResponse(path, media_type="application/pdf", filename=path.name,
                        content_disposition_type="inline")


@app.get("/api/interview/{iid}")
def api_interview(iid: str):
    return {"prep": db.get_interview_prep(iid), "job": _interview_jobs.get(iid)}


@app.post("/interview/{iid}/delete")
def interview_delete(iid: str):
    db.delete_interview_prep(iid)
    return RedirectResponse("/interview", status_code=303)


# ---- api -----------------------------------------------------------------
@app.get("/api/material/{mid}")
def api_material(mid: str):
    return {"material": db.get_material(mid), "sections": db.get_sections(mid),
            "stats": db.stats(mid), "job": _jobs.get(mid)}


@app.get("/api/email/diagnose")
def api_diagnose():
    return email_delivery.diagnose(config.load_settings())

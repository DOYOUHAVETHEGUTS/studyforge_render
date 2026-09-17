"""
Interview-prep generation.

Two direct Messages API calls (same engine as the rest of the pipeline -- no Managed
Agent sessions):

  1. prep_agent      -> a full markdown interview-prep document, structured like a
                        recruiter/coach one-pager: what the role tests, the candidate's
                        proof points mapped to the posting's requirements, a concept
                        refresher, likely questions, a live-case framework, and a closing
                        positioning line.
  2. questions_agent -> a structured JSON list of {question, approach} the interactive
                        study page flips through, so the user can rehearse out loud.

Both are grounded ONLY in the three provided inputs (job posting, resume, notes) -- the
model is told not to invent experience the resume doesn't contain.
"""
from .agents import AnthropicClient, LLMError, _extract_json  # noqa: F401 (AnthropicClient re-exported for callers)

PREP_SYSTEM = (
    "You are an experienced interview coach preparing a specific candidate for a specific "
    "role. Using ONLY the job posting, the candidate's resume, and their notes, write a "
    "focused markdown interview-prep document. Structure it with these sections:\n"
    "1. What this specific role is testing (read the posting closely; call out 2-4 themes).\n"
    "2. Proof points mapped to their requirements — a two-column-style list pairing each "
    "key responsibility from the posting with concrete evidence FROM THE RESUME. Name the "
    "candidate's single strongest story for this room.\n"
    "3. Concept refresher — key terms/formulas relevant to this role, stated concisely.\n"
    "4. Likely questions — behavioral, case/reasoning, and rapid technical, grouped.\n"
    "5. A framework for structuring a live case answer out loud.\n"
    "6. A closing positioning line the candidate can say.\n\n"
    "Rules: ground every proof point in the resume — do NOT invent experience the resume "
    "doesn't show. If the resume is thin on something the role wants, say so honestly and "
    "suggest how to address the gap. Keep it practical and specific to this posting, not "
    "generic interview advice."
)

QUESTIONS_SYSTEM = (
    "You generate likely interview questions for a specific role, with a short approach for "
    "each so the candidate can rehearse. Using ONLY the job posting, resume, and notes, "
    'respond with ONLY JSON: {"questions": [{"q": str, "type": '
    '"behavioral|case|technical", "approach": str}]}. '
    "The approach should reference the candidate's actual experience where relevant, and be "
    "1-3 sentences. Produce 10-15 questions weighted toward the interview style implied by "
    "the posting. No prose, no code fences."
)


def _inputs_block(job_posting, resume_text, notes):
    return (f"--- JOB POSTING ---\n{job_posting or '(none provided)'}\n\n"
            f"--- CANDIDATE RESUME ---\n{resume_text or '(none provided)'}\n\n"
            f"--- CANDIDATE NOTES ---\n{notes or '(none provided)'}")


def generate_prep_markdown(client, *, role_title, company, job_posting, resume_text, notes):
    header = f"Role: {role_title}" + (f" at {company}" if company else "")
    user = f"{header}\n\n{_inputs_block(job_posting, resume_text, notes)}"
    return client.call(PREP_SYSTEM, user)


def generate_questions(client, *, role_title, company, job_posting, resume_text, notes):
    header = f"Role: {role_title}" + (f" at {company}" if company else "")
    user = f"{header}\n\n{_inputs_block(job_posting, resume_text, notes)}"
    data = _extract_json(client.call(QUESTIONS_SYSTEM, user, max_tokens=3000))
    # tolerate either {"questions": [...]} or a bare [...]
    if isinstance(data, dict):
        return data.get("questions", [])
    return data if isinstance(data, list) else []

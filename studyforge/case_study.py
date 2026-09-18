"""
Case-study generation & scoring for Interview Prep (direct Messages API, same engine
as the rest of StudyForge).

Key design point from the spec: solutions are NOT a single rigid "correct answer."
Each question carries a few ACCEPTABLE APPROACHES (different valid frameworks a strong
candidate might use), and scoring checks the learner's response against whichever
approach(es) it most aligns with -- or credits a reasonable approach not listed. This
mirrors how real case interviews are actually graded.

  suggest_case_types() -> role-tailored case type options for the create UI
  generate_case_study() -> scenario + questions (practice-facing) + solutions (hidden,
                           used for the PDF's last page and for scoring)
  score_case_attempt()  -> per-question verdicts + resume-grounded gap analysis
"""
from .agents import AnthropicClient, LLMError, _extract_json  # noqa: F401

TYPES_SYSTEM = (
    "You suggest case-interview types appropriate for a specific job posting. Respond with "
    'ONLY JSON: {"types": [{"key": str, "label": str, "description": str}]}. '
    "key is a short snake_case id (e.g. market_sizing, profitability, data_operations, "
    "partnership_evaluation, strategy_growth, technical_design). Produce 4-5 options that "
    "genuinely fit what this role would test in a case interview — read the posting closely "
    "rather than defaulting to generic consulting cases if the role doesn't call for them."
)

GENERATE_SYSTEM = (
    "You write realistic case-interview exercises for a specific job posting. Produce a "
    "scenario and questions the candidate would practice with, PLUS separate solution "
    "guidance that will only be shown after they attempt it (or on a hidden answer-key page). "
    'Respond with ONLY JSON: {"title": str, "scenario_md": str, "questions": '
    '[{"id": str, "question": str}], "solutions": [{"question_id": str, "approaches": '
    '[{"name": str, "key_points": [str]}], "model_notes": str}]}.\n\n'
    "Rules:\n"
    "- scenario_md is markdown: business/technical context, data given, then the numbered "
    "questions. It must contain NO answers or hints at the solution.\n"
    "- Every question needs 2-3 DIFFERENT valid approaches in solutions, not one 'correct' "
    "answer — real case questions usually have multiple legitimate frameworks. key_points "
    "are the specific things a strong answer using that approach would include.\n"
    "- Match difficulty to the requested level: easier = more structure/guidance in the "
    "scenario and narrower scope; harder = more ambiguity, the candidate must structure the "
    "problem themselves, tighter time pressure implied.\n"
    "- Ground the scenario in the actual job posting's domain/responsibilities — not a "
    "generic case unrelated to the role."
)

SCORE_SYSTEM = (
    "You grade a candidate's case-interview responses. For each question, you're given the "
    "candidate's answer and a few DIFFERENT acceptable approaches (not one rigid answer). "
    "Judge which approach (if any) their answer aligns with, or credit a reasonable approach "
    "not listed if their reasoning is sound. Respond with ONLY JSON: "
    '{"per_question": [{"question_id": str, "verdict": "correct"|"partial"|"incorrect", '
    '"matched_approach": str|null, "matched_points": [str], "missing_points": [str], '
    '"feedback": str}], "gaps": [str], "overall_summary": str, "score_percent": float}.\n\n'
    "Rules:\n"
    "- verdict='correct' when the answer clearly hits an approach's key points (doesn't need "
    "to be verbatim). 'partial' when on the right track but missing important pieces. "
    "'incorrect' when it misses the point of the question or shows a misunderstanding.\n"
    "- feedback: if correct, briefly note WHAT they included that satisfied the answer. If "
    "partial/incorrect, give detailed, specific feedback — what a strong answer would have "
    "covered and why theirs fell short.\n"
    "- gaps: 0-4 items. Using the candidate's RESUME, call out where their answers suggest a "
    "genuine experience gap relevant to this job posting (e.g. 'no evidence of experience "
    "with X, which this case and the role both require') — only include gaps with real "
    "textual support, don't speculate.\n"
    "- score_percent: correct=1.0, partial=0.5, incorrect=0.0 per question, averaged, as a "
    "0-100 number.\n"
    "- These are practice estimates from an AI grader, not an official interview outcome — "
    "keep tone constructive and specific, not harsh."
)


def suggest_case_types(client, *, role_title, company, job_posting):
    user = f"Role: {role_title}{f' at {company}' if company else ''}\n\n{job_posting[:6000]}"
    data = _extract_json(client.call(TYPES_SYSTEM, user, max_tokens=1200))
    return data.get("types", []) if isinstance(data, dict) else (data or [])


def generate_case_study(client, *, role_title, company, job_posting, resume_text,
                        case_type_label, difficulty):
    user = (f"Role: {role_title}{f' at {company}' if company else ''}\n"
            f"Case type: {case_type_label}\nDifficulty: {difficulty}\n\n"
            f"--- JOB POSTING ---\n{job_posting[:8000]}\n\n"
            f"--- CANDIDATE RESUME (for realistic context only; not needed in the scenario) ---\n"
            f"{resume_text[:3000] if resume_text else '(none provided)'}")
    return _extract_json(client.call(GENERATE_SYSTEM, user, max_tokens=4000))


def score_case_attempt(client, *, role_title, job_posting, resume_text, questions_with_solutions,
                       responses_by_qid):
    blocks = []
    for q in questions_with_solutions:
        qid = q["question_id"]
        approaches = "\n".join(
            f"  - {a['name']}: {', '.join(a.get('key_points', []))}" for a in q.get("approaches", []))
        answer = responses_by_qid.get(qid, "(no answer given)")
        blocks.append(f"[Question {qid}] {q.get('question_text','')}\n"
                      f"Acceptable approaches:\n{approaches}\n"
                      f"Candidate's answer: {answer}")
    user = (f"Role: {role_title}\n\n--- JOB POSTING (excerpt) ---\n{job_posting[:3000]}\n\n"
            f"--- CANDIDATE RESUME ---\n{resume_text[:3000] if resume_text else '(none provided)'}\n\n"
            + "\n\n".join(blocks))
    return _extract_json(client.call(SCORE_SYSTEM, user, max_tokens=4000))

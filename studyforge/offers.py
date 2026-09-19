"""
Offer comparison agents (direct Messages API, same engine as the rest of StudyForge).

Two staged calls:
  generate_questions() -> 7-10 questions that surface the real pros/cons tradeoffs across
                          the offers. A deliberate MIX: some general priority questions
                          (what matters to you?) and some offer-specific ones tied to a
                          concrete detail of one offer (its relocation, its comp structure).
  rank_offers()        -> ranks best-to-least-attractive using the candidate's answers,
                          with pros, cons, and an explicit rationale per offer.

Grounding rules baked into the prompts: reason only from the supplied offers, résumé, and
answers; do not invent compensation, benefits, or company facts that weren't provided, and
say when something important is unknown rather than assuming it.
"""
from .agents import AnthropicClient, LLMError, _extract_json  # noqa: F401

QUESTIONS_SYSTEM = (
    "You help a candidate decide between competing job offers. Given the offers and the "
    "candidate's background, write 7-10 questions whose answers will genuinely discriminate "
    "between these specific offers.\n\n"
    "Produce a MIX:\n"
    "  - general questions about the candidate's priorities and constraints (comp vs growth, "
    "relocation tolerance, risk appetite, work style, timeline), and\n"
    "  - offer-specific questions tied to a CONCRETE detail of one named offer (e.g. its "
    "relocation package, equity structure, team size, or an unusual responsibility).\n\n"
    'Respond with ONLY JSON: {"questions": [{"id": str, "question": str, "kind": '
    '"general"|"offer_specific", "offer_label": str|null, "why_it_matters": str}]}. '
    "Use short ids like q1, q2. offer_label must exactly match one of the provided offer "
    "labels for offer_specific questions, and be null for general ones. "
    "Ask about tradeoffs that are actually in tension across these offers — do not pad with "
    "generic career-advice questions. Do not invent facts not present in the offers."
)

RANK_SYSTEM = (
    "You rank competing job offers for a candidate based on their own stated priorities and "
    "answers. Rank best to least attractive FOR THIS CANDIDATE — not by prestige or pay alone.\n\n"
    'Respond with ONLY JSON: {"ranking": [{"offer_label": str, "rank": int, "fit_score": int, '
    '"pros": [str], "cons": [str], "rationale": str, "unknowns": [str]}], '
    '"summary": str, "decision_drivers": [str]}. '
    "rank starts at 1 (best). fit_score is 0-100. Every pro/con must trace to something in "
    "the offers, the résumé, or the candidate's answers — never invent compensation, benefits, "
    "or company details. Put genuinely important missing information in 'unknowns' (e.g. "
    "'equity value not provided') rather than guessing at it. decision_drivers should name the "
    "2-4 factors that actually decided the ordering, so the candidate can sanity-check the logic "
    "against their own judgment."
)


def _offers_block(offers):
    parts = []
    for o in offers:
        parts.append(
            f"### Offer: {o['label']}\n"
            f"Company: {o.get('company') or '(not given)'}\n"
            f"Role: {o.get('role_title') or '(not given)'}\n"
            f"Compensation: {o.get('compensation') or '(not given)'}\n"
            f"Benefits: {o.get('benefits') or '(not given)'}\n"
            f"Relocation: {o.get('relocation') or '(not given)'}\n"
            f"Other considerations: {o.get('other_notes') or '(none)'}\n"
            f"Job posting:\n{(o.get('job_posting') or '(not provided)')[:4000]}")
    return "\n\n".join(parts)


def generate_questions(client, *, offers, resume_text, priorities, n_min=7, n_max=10):
    user = (f"Candidate résumé/background:\n{resume_text or '(not provided)'}\n\n"
            f"What the candidate says matters to them:\n{priorities or '(not stated)'}\n\n"
            f"Offers under consideration ({len(offers)}):\n\n{_offers_block(offers)}\n\n"
            f"Write between {n_min} and {n_max} questions.")
    data = _extract_json(client.call(QUESTIONS_SYSTEM, user, max_tokens=4000))
    if isinstance(data, dict):
        return data.get("questions", [])
    return data if isinstance(data, list) else []


def rank_offers(client, *, offers, resume_text, priorities, questions, answers_by_id):
    qa_lines = []
    for q in questions:
        ans = (answers_by_id.get(q.get("id"), "") or "").strip()
        tag = f" [about {q['offer_label']}]" if q.get("offer_label") else " [general]"
        qa_lines.append(f"Q{tag}: {q.get('question','')}\nA: {ans or '(no answer given)'}")
    user = (f"Candidate résumé/background:\n{resume_text or '(not provided)'}\n\n"
            f"Stated priorities:\n{priorities or '(not stated)'}\n\n"
            f"Offers ({len(offers)}):\n\n{_offers_block(offers)}\n\n"
            f"The candidate's answers:\n\n" + "\n\n".join(qa_lines))
    return _extract_json(client.call(RANK_SYSTEM, user, max_tokens=8000))

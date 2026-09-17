"""
Language-learning agents (direct Messages API, same engine as the rest of StudyForge).

Staged, not one mega-prompt:
  diagnose()            -> estimate per-skill level from the learner's context/evidence
  extract_curriculum()  -> structured units/grammar/vocab from a textbook/notes (NOT a summary)
  build_path()          -> adaptive learning path from diagnostic + available curriculum
  generate_prompts()    -> speaking/writing prompts adapted to language/level/goal/weaknesses
  score_production()    -> the reusable assessment engine (Fluency&Coherence, Lexical Resource,
                           Grammatical Range&Accuracy, Pronunciation) + feedback

Core safeguards baked into the prompts:
  - Levels/scores are explicitly "StudyForge estimated / practice" values, never presented as
    official JLPT/TOPIK/CEFR/IELTS results.
  - Pronunciation is only scored when audio-derived signal is available; from transcript alone
    the engine returns pronunciation=null and says so.
"""
import json

from .agents import AnthropicClient, LLMError, _extract_json  # noqa: F401

# The four reusable speaking dimensions the user specified.
SPEAKING_DIMENSIONS = ["fluency", "lexical", "grammar", "pronunciation"]


def diagnose(client, *, language, goal, level_self, context, evidence_text=""):
    system = (
        "You are a language diagnostic agent. From the learner's self-description and any "
        "evidence provided, estimate their current ability. Choose the most appropriate "
        "proficiency FRAMEWORK for the language (e.g. CEFR for European languages, JLPT for "
        "Japanese, TOPIK for Korean) and place them on it. Respond with ONLY JSON: "
        '{"framework": str, "est_level": str, "skills": {"grammar": int, "vocabulary": int, '
        '"reading": int, "writing": int, "listening": int, "speaking": int}, '
        '"priorities": [str], "rationale": str}. skills are 0-100 estimates. '
        "est_level must be phrased as an ESTIMATE (e.g. 'approx. CEFR A2') — never claim an "
        "official test result. Base estimates on evidence; if evidence is thin, say so in "
        "rationale and estimate conservatively."
    )
    user = (f"Language: {language}\nGoal: {goal}\nSelf-reported level: {level_self}\n\n"
            f"Learner's description:\n{context or '(none)'}\n\n"
            f"Additional evidence (notes/results/etc.):\n{evidence_text or '(none)'}")
    return _extract_json(client.call(system, user, max_tokens=1500))


def extract_curriculum(client, *, language, material_text):
    system = (
        "You are a curriculum extraction agent. Given text from a language textbook or study "
        "notes, produce a STRUCTURED representation of what it teaches — NOT a summary. "
        'Respond with ONLY JSON: {"units": [{"title": str, "grammar": [str], "vocabulary": '
        '[str], "skills": [str]}], "progression_notes": str}. Keep vocabulary items short '
        "(words/phrases, not definitions). If the text is fragmentary (e.g. photographed "
        "notes), extract what is clearly legible and note uncertainty in progression_notes."
    )
    user = f"Language: {language}\n\n--- MATERIAL TEXT ---\n{material_text[:14000]}"
    return _extract_json(client.call(system, user, max_tokens=4000))


def extract_from_images(client, *, language, image_blocks):
    """image_blocks: list of Anthropic image content blocks. Returns extracted concepts +
    an explicit uncertainty flag so the UI can ask the learner to review before trusting it."""
    system = (
        "You extract language-learning content from photos of notes, textbook pages, "
        "worksheets, or flashcards. Read what you can; do NOT guess at illegible handwriting. "
        'Respond with ONLY JSON: {"vocabulary": [str], "grammar": [str], "concepts": [str], '
        '"uncertain": bool, "uncertainty_notes": str}. Set uncertain=true and explain in '
        "uncertainty_notes whenever image quality or handwriting makes extraction unreliable."
    )
    content = list(image_blocks) + [
        {"type": "text", "text": f"Language: {language}. Extract learning content as specified."}]
    # images require the raw messages API shape, so call the SDK directly through the client
    return _extract_json(client.call_multimodal(system, content, max_tokens=2000))


def build_path(client, *, language, goal, diagnostic, curriculum=None):
    system = (
        "You are a learning-path agent. Using the diagnostic (what the learner knows and their "
        "weaknesses) and any available curriculum (what CAN be taught), build an adaptive path. "
        'Respond with ONLY JSON: {"immediate_priorities": [str], "current_unit": {"title": str, '
        '"concepts": [{"concept": str, "category": str}]}, "upcoming_units": [str], '
        '"weekly_plan": [str], "review_focus": [str]}. category is one of grammar|vocabulary|'
        "reading|writing|listening|speaking|pronunciation|culture. Prioritize the learner's "
        "weakest skills and their stated goal. If curriculum is provided, anchor units to it; "
        "otherwise generate a sensible beginner-to-goal progression for the language."
    )
    user = (f"Language: {language}\nGoal: {goal}\n\nDiagnostic:\n{json.dumps(diagnostic)[:3000]}\n\n"
            f"Available curriculum:\n{json.dumps(curriculum)[:4000] if curriculum else '(none)'}")
    return _extract_json(client.call(system, user, max_tokens=3000))


def generate_prompts(client, *, language, goal, level, kind, weaknesses=None, n=8):
    mode = "speaking" if kind == "speaking" else "writing"
    system = (
        f"You generate {mode} assessment prompts for a language learner. Produce {n} varied "
        "prompts spanning types like introduction, description, experience, opinion, scenario/"
        "role-play, and narrative, adapted to the language, level, and goal. Respond with ONLY "
        'JSON: {"prompts": [{"type": str, "prompt": str}]}. Prompts should be answerable by a '
        "learner at the stated level and should probe their known weaknesses where given."
    )
    user = (f"Language: {language}\nLevel: {level}\nGoal: {goal}\n"
            f"Known weaknesses: {', '.join(weaknesses) if weaknesses else '(none yet)'}")
    data = _extract_json(client.call(system, user, max_tokens=2000))
    return data.get("prompts", []) if isinstance(data, dict) else (data or [])


def score_production(client, *, language, level, responses, audio_based=False):
    """The reusable assessment engine. `responses` = list of {prompt, text}. Scores the four
    speaking dimensions on 0-9 (half points). Pronunciation is only scored when audio_based."""
    pron_rule = (
        "Pronunciation: score 0-9 based on the audio-derived notes provided."
        if audio_based else
        "Pronunciation: you were given TRANSCRIPTS ONLY, so you CANNOT judge pronunciation — "
        'return null for pronunciation and note this in feedback.')
    system = (
        "You are a language production assessor. Evaluate the learner's responses across four "
        "dimensions on a 0-9 scale (half-points allowed): fluency (fluency & coherence), "
        "lexical (lexical resource), grammar (grammatical range & accuracy), and pronunciation. "
        f"{pron_rule} Respond with ONLY JSON: {{\"scores\": {{\"fluency\": float, \"lexical\": "
        "float, \"grammar\": float, \"pronunciation\": float|null, \"overall\": float}, "
        '"per_response": [{"strengths": str, "corrections": [str], "better_way": str, '
        '"one_thing": str}], "recurring_errors": [str]}. These are PRACTICE estimates, not '
        "official exam scores. Prioritize the few errors that most affect communication; do "
        "not overwhelm with dozens of minor corrections."
    )
    body = "\n\n".join(f"[Prompt {i+1}] {r.get('prompt','')}\nResponse: {r.get('text','')}"
                       for i, r in enumerate(responses))
    user = f"Language: {language}\nEstimated level: {level}\n\n{body}"
    return _extract_json(client.call(system, user, max_tokens=3000))

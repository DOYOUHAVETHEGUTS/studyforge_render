"""
The four StudyForge agents.

Each agent is a thin, testable Python function around a single model call. They pass
plain dicts to each other. They are orchestrated per-section in pipeline.py, run
sequentially within a section and in a loop across sections.

  Agent 1  structure_agent    -> section map / material understanding (fallback only)
  Agent 2  summarize_agent    -> grounded study summary (markdown)
  Agent 3  learning_agent     -> flashcards + quiz questions (JSON)
  Agent 4  grounding_agent    -> verify summary is supported by source (JSON verdict)

The prompts here are the canonical, in-code versions. The standalone
agent_prompts/*.md files are the same prompts formatted for the Claude Console so
you can iterate on them independently.
"""
import json
import re
import time


class LLMError(Exception):
    pass


class AnthropicClient:
    def __init__(self, api_key, model="claude-sonnet-5", max_tokens=8000, max_retries=3):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self._client = None

    def _ensure(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)

    def call(self, system, user, max_tokens=None):
        """Single call with exponential backoff. Raises LLMError after retries."""
        return self._create(system, [{"role": "user", "content": user}], max_tokens)

    def call_multimodal(self, system, content_blocks, max_tokens=None):
        """Call with a mixed content list (text + image blocks), for photo/notes extraction."""
        return self._create(system, [{"role": "user", "content": content_blocks}], max_tokens)

    def _create(self, system, messages, max_tokens=None):
        self._ensure()
        last = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens or self.max_tokens,
                    system=system,
                    messages=messages,
                )
                return "".join(b.text for b in resp.content if b.type == "text")
            except Exception as e:  # noqa: BLE001 - SDK raises several types
                last = e
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
        raise LLMError(f"model call failed after {self.max_retries} attempts: {last}")


def _extract_json(text):
    """Pull the first JSON object/array out of a model response."""
    text = text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.M).strip()
    m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
    if not m:
        raise LLMError("no JSON found in model response")
    return json.loads(m.group(1))


# --------------------------------------------------------------------------
# Agent 1 — Structure (fallback when deterministic sectioning is weak)
# --------------------------------------------------------------------------
def structure_agent(client, name, text, material_type, system_override=None):
    system = system_override or (
        "You are a document-structure analyst. Given the opening of a learning "
        "resource, identify its logical divisions. Respond with ONLY a JSON array of "
        '{"title": str, "kind": "chapter|unit|section|topic"} in reading order. '
        "No prose, no code fences."
    )
    user = (f"Material name: {name}\nType: {material_type}\n\n"
            f"--- BEGIN EXCERPT ---\n{text[:8000]}\n--- END EXCERPT ---")
    return _extract_json(client.call(system, user, max_tokens=1500))


# --------------------------------------------------------------------------
# Agent 2 — Summarization (grounded study summary)
# --------------------------------------------------------------------------
def summarize_agent(client, *, title, text, material_type, target_pages=4,
                    exam_name="", objectives="", focus="", system_override=None):
    if material_type == "narrative":
        system = (
            "You are a literature study-guide writer. For the given portion of a novel or "
            "narrative work, produce a markdown study note grounded ONLY in the provided "
            "text: a brief plot summary, key characters introduced or developed, important "
            "events, notable themes/motifs, and 2-3 discussion questions. Do NOT spoil "
            "beyond the provided text and do NOT invent events not present in it."
        )
        ask = f"Section: {title}\nEmphasis: {focus or 'balanced'}"
        user = f"{ask}\n\n--- BEGIN SOURCE ---\n{text}\n--- END SOURCE ---"
        return client.call(system_override or system, user)
    if material_type == "language":
        system = (
            "You are a language-learning content specialist. Produce a STUDY SHEET "
            "grounded ONLY in the provided source. Prefer structure over prose: use "
            "markdown tables for vocabulary (term | translation | notes), a bulleted "
            "grammar-rules list, a verb-conjugation table when present, and 3-6 example "
            "sentences drawn from the source. Do not invent words not in the source."
        )
        ask = f"Unit: {title}\nEmphasis: {focus or 'balanced'}"
    else:
        exam_line = f"Align to the {exam_name} exam objectives.\n{objectives}\n" if exam_name else ""
        system = (
            "You are an expert instructor writing exam-focused study guides. Write a "
            f"~{target_pages}-page markdown summary grounded ONLY in the provided source "
            "text. Use clear headings, high-yield bullet points, and a short "
            "'Key facts to memorize' section. Do not introduce facts absent from the "
            "source. If the source is thin, say so rather than padding."
        )
        ask = f"{exam_line}Section: {title}\nEmphasis: {focus or 'balanced coverage'}"
    user = f"{ask}\n\n--- BEGIN SOURCE ---\n{text}\n--- END SOURCE ---"
    return client.call(system_override or system, user)


# --------------------------------------------------------------------------
# Agent 3 — Learning artifacts (flashcards + quiz)
# --------------------------------------------------------------------------
def learning_agent(client, *, title, summary_md, n_cards=8, n_quiz=4, system_override=None):
    system = system_override or (
        "You create study artifacts from an existing summary. Respond with ONLY JSON: "
        '{"flashcards":[{"q":str,"a":str}],"quiz":[{"q":str,"choices":[str,str,str,str],'
        '"answer_index":int}]}. Base everything on the summary; no outside facts.'
    )
    user = (f"Make {n_cards} flashcards and {n_quiz} multiple-choice quiz questions "
            f"for '{title}'.\n\n--- SUMMARY ---\n{summary_md}")
    return _extract_json(client.call(system, user, max_tokens=2500))


# --------------------------------------------------------------------------
# Agent 4 — Grounding / QA (verify summary faithfulness)
# --------------------------------------------------------------------------
def grounding_agent(client, *, summary_md, source_text, system_override=None):
    system = system_override or (
        "You are a fact-grounding checker. Determine whether each substantive claim in "
        "the SUMMARY is supported by the SOURCE. Respond with ONLY JSON: "
        '{"grounded": bool, "unsupported_claims": [str], "score": float}. '
        "score is fraction of claims supported (0..1). Be strict but fair; ignore "
        "generic framing sentences."
    )
    user = (f"--- SOURCE ---\n{source_text[:12000]}\n\n"
            f"--- SUMMARY ---\n{summary_md}")
    return _extract_json(client.call(system, user, max_tokens=1500))

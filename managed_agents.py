"""
Claude Managed Agents integration — config fetch only.

StudyForge does NOT run its four agents as Managed Agent *sessions*. That runtime is
built for autonomous, tool-using, sandboxed work and bills per session-hour on top of
token costs -- overkill for a single stateless completion per section.

Instead, this module does a one-time GET against the Managed Agents API to read back
an agent's persisted system prompt, so you can point Settings at an `agent_...` ID you
built in Console and pull its prompt text in with one click, instead of copy-pasting it
by hand. The pipeline then uses that fetched text exactly like any other prompt
override, via the ordinary Messages API (see agents.py / pipeline.py).

NOTE: verified against public documentation as of Aug 2026, not against a live call --
Managed Agents is a beta API and this repo doesn't have a live agent ID/key to test
against. If Anthropic's response shape differs from what's assumed here, sync_agent_id()
surfaces the raw response so the mismatch is easy to diagnose and fix.
"""
import requests

MANAGED_AGENTS_BETA_HEADER = "managed-agents-2026-04-01"
_LIKELY_PROMPT_KEYS = ("system_prompt", "system", "instructions")


class ManagedAgentsError(Exception):
    pass


def fetch_agent_config(agent_id: str, api_key: str) -> dict:
    if not agent_id.strip():
        raise ManagedAgentsError("No agent ID provided.")
    if not api_key:
        raise ManagedAgentsError("No Anthropic API key configured — set one in Settings first.")

    resp = requests.get(
        f"https://api.anthropic.com/v1/agents/{agent_id.strip()}",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": MANAGED_AGENTS_BETA_HEADER,
        },
        timeout=20,
    )
    if resp.status_code == 404:
        raise ManagedAgentsError(f"No agent found with ID '{agent_id}'. Check it's copied exactly.")
    if resp.status_code == 401:
        raise ManagedAgentsError("Anthropic API key rejected — check it in Settings.")
    if resp.status_code >= 300:
        raise ManagedAgentsError(f"Managed Agents API returned {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def extract_system_prompt(agent_config: dict) -> str:
    for key in _LIKELY_PROMPT_KEYS:
        val = agent_config.get(key)
        if isinstance(val, str) and val.strip():
            return val
    raise ManagedAgentsError(
        "Fetched the agent, but couldn't find its system prompt in the expected fields "
        f"({', '.join(_LIKELY_PROMPT_KEYS)}). Raw response: {str(agent_config)[:400]}")


def sync_agent_id(agent_id: str, api_key: str) -> str:
    """Fetch + extract in one call. Raises ManagedAgentsError with a clear message on failure."""
    config = fetch_agent_config(agent_id, api_key)
    return extract_system_prompt(config)

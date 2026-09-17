"""
Smoke test — runs the whole pipeline with the LLM stubbed out, so it needs no
API key and no network. Verifies ingest -> structure -> DB -> render -> status.

    python -m pytest tests/ -q      (or)     python tests/test_smoke.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from studyforge import config, db, pipeline, structure  # noqa: E402


def _sample_text():
    parts = []
    for n, title in [(1, "Chapter 1 Introduction"), (2, "Chapter 2 Core Concepts"),
                     (3, "Chapter 3 Advanced Topics")]:
        parts.append(title)
        parts.append(" ".join(["content"] * 500))
    return "\n".join(parts)


def test_structure_and_pipeline(tmp_path=None):
    db.init_db()

    # 1. write a sample .txt material and add it (no LLM)
    src = config.UPLOADS_DIR / "sample_material.txt"
    src.write_text(_sample_text(), encoding="utf-8")
    mid = pipeline.add_material("Smoke Test Book", str(src), declared_type="textbook")

    secs = db.get_sections(mid)
    assert len(secs) >= 2, f"expected multiple sections, got {len(secs)}"
    print(f"[ok] sectioned into {len(secs)} sections")

    # 2. stub the agents so no API/network is used
    import studyforge.pipeline as P

    def fake_summarize(client, material, sec, srctext, wc, settings=None):
        return f"# {sec['title']}\n\n## Overview\nStub summary.\n\n- point one\n- point two"

    P.summarize_agent_call = fake_summarize
    P._safe_ground = lambda *a, **k: {"grounded": True, "unsupported_claims": [], "score": 1.0}
    P._safe_learning = lambda *a, **k: None

    class DummyClient:
        pass

    import studyforge.agents as A
    A.AnthropicClient = lambda *a, **k: DummyClient()

    import os
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

    stats = pipeline.process_material(mid, make_learning=False)
    print(f"[ok] processed: {stats}")
    assert stats["done"] == stats["total"] and stats["failed"] == 0

    # 3. verify PDFs + MD exist
    for s in db.get_sections(mid):
        assert s["status"] == "generated"
        assert config.abspath(s["summary_pdf"]).exists()
        assert config.abspath(s["summary_md"]).exists()
    print("[ok] all summaries rendered to md + pdf")

    # cleanup
    db.delete_material(mid)
    print("[ok] smoke test passed")


if __name__ == "__main__":
    test_structure_and_pipeline()

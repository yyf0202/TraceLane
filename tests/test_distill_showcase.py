from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "distill_research_showcase.py"


def fullflow(decision: str = "Hold") -> dict[str, object]:
    return {
        "ticker": "600570.SH",
        "date": "2026-07-10",
        "decision": decision,
        "selected_analysts": ["market", "social", "news", "fundamentals", "industry"],
        "state": {
            "company_of_interest": "600570.SH",
            "trade_date": "2026-07-10",
            "market_report": "market " * 400,
            "sentiment_report": "sentiment " * 300,
            "news_report": "news " * 380,
            "fundamentals_report": "fundamentals " * 350,
            "industry_report": "industry " * 1500,
            "investment_debate": {"bull_history": "bull " * 500, "bear_history": "bear " * 540},
            "risk_debate": {"aggressive_history": "a " * 200, "conservative_history": "c " * 250, "neutral_history": "n " * 220},
        },
    }


def run_distill(tmp_path: Path, decision: str = "Hold") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "fullflow.json"
    src.write_text(json.dumps(fullflow(decision)), encoding="utf-8")
    out = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(src), "--out", str(out)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    produced = list(out.glob("*.json"))
    assert len(produced) == 1
    return produced[0]


def test_distill_produces_sanitized_task(tmp_path: Path) -> None:
    task_path = run_distill(tmp_path)
    task = json.loads(task_path.read_text(encoding="utf-8"))
    # Real identity is gone.
    assert "600570" not in json.dumps(task)
    assert task["task_id"].startswith("SHOWCASE-")
    # Structure preserved.
    assert [a["analyst_id"] for a in task["analysts"]] == [
        "market", "social", "news", "fundamentals", "industry",
    ]
    assert len(task["evidence"]) == 5
    # Prose is synthetic, tagged by source type only.
    for record in task["evidence"]:
        assert "Synthetic" in record["text"]
        assert record["source"].startswith("tracelane-showcase-")


def test_distill_is_deterministic(tmp_path: Path) -> None:
    first = run_distill(tmp_path / "a").read_bytes()
    second = run_distill(tmp_path / "b").read_bytes()
    assert first == second


def test_hold_distills_to_standoff(tmp_path: Path) -> None:
    task = json.loads(run_distill(tmp_path, "Hold").read_text(encoding="utf-8"))
    directions = [a["direction_hint"] for a in task["analysts"]]
    assert task["resolution"]["actual_direction"] == "neutral"
    # A hold is a standoff: both sides present, not a one-sided lean.
    assert "bullish" in directions and "bearish" in directions


def test_decisive_run_distills_to_consensus(tmp_path: Path) -> None:
    task = json.loads(run_distill(tmp_path, "Buy").read_text(encoding="utf-8"))
    assert task["resolution"]["actual_direction"] == "bullish"
    assert task["resolution"]["metric_value"] > 0

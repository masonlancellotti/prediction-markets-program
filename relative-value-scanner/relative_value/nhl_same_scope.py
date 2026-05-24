from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from relative_value.nhl_scope import NHL_STANLEY_CUP_SCOPE, nhl_stanley_cup_profile


SCHEMA_VERSION = 1
DISCLAIMER = "Saved-file NHL Stanley Cup pair generator only. Diagnostics only; no execution or profit claim."


def build_nhl_stanley_cup_pairs_files(
    *,
    polymarket_snapshot_path: Path,
    kalshi_snapshot_path: Path,
    json_output_path: Path,
    markdown_output_path: Path,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    polymarket = _load_json_object(polymarket_snapshot_path, "polymarket_snapshot")
    kalshi = _load_json_object(kalshi_snapshot_path, "kalshi_snapshot")
    payload = build_nhl_stanley_cup_pairs_report(
        polymarket_snapshot=polymarket,
        kalshi_snapshot=kalshi,
        generated_at=generated_at,
        inputs={"polymarket": str(polymarket_snapshot_path), "kalshi": str(kalshi_snapshot_path)},
        outputs={"json": str(json_output_path), "markdown": str(markdown_output_path)},
    )
    json_output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output_path.write_text(render_nhl_stanley_cup_pairs_markdown(payload), encoding="utf-8")
    return payload


def build_nhl_stanley_cup_pairs_report(
    *,
    polymarket_snapshot: dict[str, Any],
    kalshi_snapshot: dict[str, Any],
    generated_at: datetime | None = None,
    inputs: dict[str, str] | None = None,
    outputs: dict[str, str] | None = None,
) -> dict[str, Any]:
    generated = generated_at or datetime.now(timezone.utc)
    poly_rows = [_prepared(row, "polymarket") for row in _market_rows(polymarket_snapshot)]
    kalshi_rows = [_prepared(row, "kalshi") for row in _market_rows(kalshi_snapshot)]
    poly_sc = [row for row in poly_rows if row["profile"]["scope"] == NHL_STANLEY_CUP_SCOPE]
    kalshi_sc = [row for row in kalshi_rows if row["profile"]["scope"] == NHL_STANLEY_CUP_SCOPE]
    pairs: list[dict[str, Any]] = []
    rejected_pairs: list[dict[str, Any]] = []
    for poly in poly_sc:
        for kalshi in kalshi_sc:
            reason = _reject_reason(poly["profile"], kalshi["profile"])
            if reason is None:
                pairs.append(_pair(poly, kalshi))
            else:
                rejected_pairs.append(_rejected_pair(poly, kalshi, reason))
    pairs.sort(key=lambda row: ((row.get("matched_team") or {}).get("team_id") or "", _poly_id(row), _kalshi_ticker(row)))
    rejected_rows = [_rejected_row(row) for row in [*poly_rows, *kalshi_rows] if row["profile"]["scope"] != NHL_STANLEY_CUP_SCOPE]
    rejection_counts = Counter(row["reason"] for row in rejected_rows)
    rejection_counts.update(row["reason"] for row in rejected_pairs)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "nhl_stanley_cup_saved_pair_generator_v1",
        "generated_at": generated.isoformat(),
        "diagnostic_only": True,
        "pair_count": len(pairs),
        "pairs": pairs,
        "summary": {
            "generated_stanley_cup_pair_count": len(pairs),
            "source_counts_by_scope": {
                "polymarket": dict(Counter(row["profile"]["scope"] for row in poly_rows)),
                "kalshi": dict(Counter(row["profile"]["scope"] for row in kalshi_rows)),
            },
            "matched_team_entity_pairs": [pair["matched_team"] for pair in pairs],
            "rejected_row_count": len(rejected_rows),
            "rejected_candidate_pair_count": len(rejected_pairs),
            "rejected_reasons": dict(sorted(rejection_counts.items())),
        },
        "rejected_rows": rejected_rows[:100],
        "rejected_candidate_pairs": rejected_pairs[:100],
        "inputs": inputs or {},
        "outputs": outputs or {},
        "safety": {
            "saved_file_only": True,
            "live_fetch_attempted": False,
            "thresholds_or_relationship_gates_lowered": False,
            "same_payoff_asserted": False,
            "promotes_subset_superset_to_same_payoff": False,
            "execution_or_trading_logic_added": False,
        },
        "disclaimer": DISCLAIMER,
    }


def render_nhl_stanley_cup_pairs_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# NHL Stanley Cup Saved Pairs",
        "",
        payload["disclaimer"],
        "",
        f"Pairs: {payload.get('pair_count', 0)}",
        "",
        "| Polymarket | Kalshi | Team | Year |",
        "|---|---|---|---|",
    ]
    for pair in payload.get("pairs") or []:
        team = pair.get("matched_team") if isinstance(pair.get("matched_team"), dict) else {}
        lines.append(
            "| "
            + " | ".join(
                [
                    _md((pair.get("polymarket") or {}).get("question")),
                    _md((pair.get("kalshi") or {}).get("question")),
                    _md(team.get("team_id")),
                    _md(team.get("championship_year")),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _pair(poly: dict[str, Any], kalshi: dict[str, Any]) -> dict[str, Any]:
    profile = poly["profile"]
    return {
        "action": "MANUAL_REVIEW",
        "competition_scope": NHL_STANLEY_CUP_SCOPE,
        "same_payoff_asserted": False,
        "research_only": True,
        "readiness_promotion": "same_scope_pair_only",
        "polymarket": {
            "market_id": str(poly["row"].get("market_id") or ""),
            "question": poly["question"],
            "event_title": poly["row"].get("event_title"),
        },
        "kalshi": {
            "ticker": str(kalshi["row"].get("ticker") or kalshi["row"].get("market_id") or ""),
            "question": kalshi["question"],
            "event_title": kalshi["row"].get("event_title"),
        },
        "matched_team": {
            "team_id": profile["team_id"],
            "championship_year": profile["championship_year"],
            "scope": NHL_STANLEY_CUP_SCOPE,
        },
        "similarity_score": 1.0,
        "ineligibility_reasons": [],
        "notes": "Manual review only. This generator makes no execution or profit claim.",
    }


def _reject_reason(poly: dict[str, Any], kalshi: dict[str, Any]) -> str | None:
    if not poly["team_id"] or not kalshi["team_id"]:
        return "team_entity_missing_or_ambiguous"
    if poly["team_id"] != kalshi["team_id"]:
        return "team_entity_mismatch"
    if not poly["championship_year"] or not kalshi["championship_year"]:
        return "championship_year_missing"
    if poly["championship_year"] != kalshi["championship_year"]:
        return "championship_year_mismatch"
    return None


def _prepared(row: dict[str, Any], source: str) -> dict[str, Any]:
    profile = nhl_stanley_cup_profile(row)
    return {
        "source": source,
        "row": row,
        "question": str(row.get("question") or row.get("title") or ""),
        "profile": profile,
    }


def _rejected_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": item["source"],
        "market_id": item["row"].get("market_id"),
        "ticker": item["row"].get("ticker"),
        "question": item["question"],
        "scope": item["profile"]["scope"],
        "team_id": item["profile"]["team_id"],
        "championship_year": item["profile"]["championship_year"],
        "reason": f"{item['source']}_scope_{str(item['profile']['scope']).lower()}_rejected",
    }


def _rejected_pair(poly: dict[str, Any], kalshi: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "reason": reason,
        "polymarket": {"market_id": poly["row"].get("market_id"), "question": poly["question"], "profile": poly["profile"]},
        "kalshi": {"ticker": kalshi["row"].get("ticker") or kalshi["row"].get("market_id"), "question": kalshi["question"], "profile": kalshi["profile"]},
    }


def _market_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("normalized_markets")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _poly_id(pair: dict[str, Any]) -> str:
    return str((pair.get("polymarket") or {}).get("market_id") or "")


def _kalshi_ticker(pair: dict[str, Any]) -> str:
    return str((pair.get("kalshi") or {}).get("ticker") or "")


def _md(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")

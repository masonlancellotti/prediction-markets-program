from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA_KIND = "batch_evidence_import_readiness_v1"

READY_CRYPTO = "READY_FOR_CRYPTO_BASIS_SCOUT"
READY_SPORTS = "READY_FOR_SPORTS_OPERATOR_SCOUT"
READY_CDNA = "READY_FOR_CDNA_FILL_FIRST_SCOUT"
READY_GRAPH = "READY_FOR_GRAPH_REVIEW"
NEEDS_RULE = "NEEDS_RULE_REVIEW"
NEEDS_RECOLLECTION = "NEEDS_RECOLLECTION"
REFERENCE_ONLY = "REFERENCE_ONLY"
IGNORE_FOR_NOW = "IGNORE_FOR_NOW"


CRYPTO_PRIORITY = {
    "btc_price_threshold": 100,
    "eth_price_threshold": 95,
    "sol_price_threshold": 90,
    "xrp_price_threshold": 85,
    "btc_deadline_touch": 80,
    "eth_deadline_touch": 75,
}

SPORTS_PRIORITY = {
    "mlb_daily": 70,
    "mlb_daily_games": 70,
    "nba_champion": 68,
    "nhl_stanley_cup": 66,
    "nfl_super_bowl": 64,
    "ucl_winner": 62,
    "french_open": 60,
    "nfl_division": 58,
    "wnba_champion": 56,
    "ufc": 54,
}


def write_batch_evidence_import_readiness_files(
    *,
    input_roots: list[Path],
    json_output: Path,
    markdown_output: Path,
) -> dict[str, Any]:
    report = build_batch_evidence_import_readiness_report(input_roots=input_roots)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_output.write_text(render_batch_evidence_import_readiness_markdown(report), encoding="utf-8")
    return report


def build_batch_evidence_import_readiness_report(*, input_roots: list[Path]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    for root in input_roots:
        if not root.exists():
            warnings.append(f"input_root_missing:{root}")
            continue
        candidates.extend(_read_index_rows(root))
        candidates.extend(_read_import_matrix(root))
        candidates.extend(_scan_family_folders(root))
    best = _dedupe(candidates)
    rows = sorted(best.values(), key=_sort_key)
    crypto_worklist = [row for row in rows if _family_group(row) == "crypto"]
    sports_worklist = [row for row in rows if _family_group(row) == "sports"]
    cdna_worklist = [row for row in rows if row.get("readiness_class") == READY_CDNA]
    graph_worklist = [row for row in rows if row.get("readiness_class") == READY_GRAPH]
    best_ready = [
        row
        for row in rows
        if row.get("readiness_class") in {READY_CRYPTO, READY_SPORTS, READY_CDNA, READY_GRAPH}
    ]
    top_tasks = _top_tasks(crypto_worklist, sports_worklist, cdna_worklist, graph_worklist)
    summary = _summary(rows)
    return {
        "schema_kind": SCHEMA_KIND,
        "diagnostic_only": True,
        "saved_files_only": True,
        "strict_exact_arb": False,
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "standard_paper_candidate_emitted": False,
        "candidate_pair_creation": False,
        "input_roots": [str(path) for path in input_roots],
        "families": rows,
        "top_next_codex_tasks": top_tasks,
        "best_ready_families": best_ready[:25],
        "crypto_worklist": crypto_worklist[:50],
        "sports_worklist": sports_worklist[:50],
        "cdna_fill_first_worklist": cdna_worklist[:50],
        "graph_worklist": graph_worklist[:50],
        "warnings": warnings,
        "summary_counts": summary,
        "top_blockers": summary["top_blockers"],
        "safety": {
            "diagnostic_only": True,
            "saved_files_only": True,
            "orders_or_execution_logic_added": False,
            "auth_or_account_logic_added": False,
            "strict_exact_arb": False,
            "exact_ready_rows": 0,
            "paper_candidate_rows": 0,
            "candidate_pair_creation": False,
        },
    }


def render_batch_evidence_import_readiness_markdown(report: dict[str, Any]) -> str:
    counts = report.get("summary_counts") or {}
    lines = [
        "# Batch Evidence Import Readiness",
        "",
        "Saved-file-only readiness matrix. It ranks evidence families for diagnostic scouts and does not create exact relationships, candidate pairs, or standard paper rows.",
        "",
        "## Summary",
        "",
        f"- families: `{counts.get('families', 0)}`",
        f"- ready_for_crypto_basis_scout: `{counts.get(READY_CRYPTO, 0)}`",
        f"- ready_for_sports_operator_scout: `{counts.get(READY_SPORTS, 0)}`",
        f"- ready_for_cdna_fill_first_scout: `{counts.get(READY_CDNA, 0)}`",
        f"- ready_for_graph_review: `{counts.get(READY_GRAPH, 0)}`",
        f"- exact_ready_rows: `0`",
        f"- paper_candidate_rows: `0`",
        "",
        "## Top Next Tasks",
        "",
    ]
    for task in report.get("top_next_codex_tasks") or []:
        lines.append(f"- `{_md(task.get('command'))}`: {_md(task.get('reason'))}")
    lines.extend(["", "## Crypto Worklist", "", "| Rank | Family | Readiness | Platforms | Blockers | Evidence |", "|---:|---|---|---|---|---|"])
    for idx, row in enumerate(report.get("crypto_worklist") or [], start=1):
        lines.append(_row_md(idx, row))
    lines.extend(["", "## Sports Worklist", "", "| Rank | Family | Readiness | Platforms | Blockers | Evidence |", "|---:|---|---|---|---|---|"])
    for idx, row in enumerate(report.get("sports_worklist") or [], start=1):
        lines.append(_row_md(idx, row))
    lines.extend(["", "## CDNA Fill-First Worklist", "", "| Rank | Family | Readiness | Platforms | Blockers | Evidence |", "|---:|---|---|---|---|---|"])
    for idx, row in enumerate(report.get("cdna_fill_first_worklist") or [], start=1):
        lines.append(_row_md(idx, row))
    lines.extend(["", "## Graph Worklist", "", "| Rank | Family | Readiness | Platforms | Blockers | Evidence |", "|---:|---|---|---|---|---|"])
    for idx, row in enumerate(report.get("graph_worklist") or [], start=1):
        lines.append(_row_md(idx, row))
    lines.extend(["", "## Top Blockers", "", "| Blocker | Count |", "|---|---:|"])
    for item in report.get("top_blockers") or []:
        lines.append(f"| {_md(item.get('blocker'))} | {_md(item.get('count'))} |")
    lines.extend(["", "## Safety", "", "- diagnostic_only: `true`", "- saved_files_only: `true`", "- exact_ready_rows: `0`", "- paper_candidate_rows: `0`"])
    return "\n".join(lines) + "\n"


def _read_index_rows(root: Path) -> list[dict[str, Any]]:
    path = root / "_index.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        family = _family_key(item.get("family") or item.get("key") or item.get("dir"))
        rows.append(
            _candidate(
                root=root,
                family=family,
                category=_category_from_item(item, family),
                readiness=_map_readiness(item.get("readiness"), item, family),
                platforms=item.get("platforms") or _platforms_from_item(item),
                blockers=item.get("blockers") or [],
                evidence_paths=_evidence_paths_from_index(root, item),
                source_kind="_index.jsonl",
            )
        )
    return rows


def _read_import_matrix(root: Path) -> list[dict[str, Any]]:
    path = root / "import_readiness_matrix.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows = []
    for item in payload.get("families") or []:
        if not isinstance(item, dict):
            continue
        family = _family_key(item.get("family"))
        rows.append(
            _candidate(
                root=root,
                family=family,
                category=_category_from_item(item, family),
                readiness=_map_readiness(item.get("readiness_class"), item, family),
                platforms=item.get("platforms_found") or item.get("platforms") or {},
                blockers=item.get("main_blockers") or item.get("blockers") or [],
                evidence_paths=_evidence_paths_from_family(root, family),
                source_kind="import_readiness_matrix.json",
            )
        )
    return rows


def _scan_family_folders(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for json_path in root.rglob("*.json"):
        if json_path.name.startswith(".") or json_path.name in {"import_readiness_matrix.json"}:
            continue
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, dict):
            continue
        family = _family_key(payload.get("market_family") or json_path.parent.name)
        category = _category_from_item(payload, family)
        platform = payload.get("platform")
        platforms = {str(platform).lower(): True} if platform else {}
        readiness = _readiness_from_payload(payload, family)
        blockers = payload.get("blockers_remaining") or payload.get("blockers") or []
        rows.append(
            _candidate(
                root=root,
                family=family,
                category=category,
                readiness=readiness,
                platforms=platforms,
                blockers=blockers,
                evidence_paths=[json_path],
                source_kind="platform_json",
            )
        )
    return rows


def _candidate(
    *,
    root: Path,
    family: str,
    category: str,
    readiness: str,
    platforms: dict[str, Any],
    blockers: list[Any],
    evidence_paths: list[Path],
    source_kind: str,
) -> dict[str, Any]:
    family = _family_key(family)
    normalized_platforms = _normalize_platforms(platforms)
    priority = _priority(family, category, readiness)
    return {
        "family": family,
        "category": category,
        "readiness_class": readiness,
        "platforms": normalized_platforms,
        "blockers": sorted({str(b) for b in blockers if str(b).strip()}),
        "evidence_paths": sorted({str(path) for path in evidence_paths}),
        "source_root": str(root),
        "source_kind": source_kind,
        "priority": priority,
        "preferred_source_score": _source_score(root, source_kind),
        "recommended_command": _recommended_command(family, readiness),
    }


def _dedupe(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for item in candidates:
        key = item["family"]
        current = best.get(key)
        if current is None or (item["priority"], item["preferred_source_score"]) > (current["priority"], current["preferred_source_score"]):
            best[key] = dict(item)
        elif current is not None:
            current["blockers"] = sorted(set(current.get("blockers") or []) | set(item.get("blockers") or []))
            current["evidence_paths"] = sorted(set(current.get("evidence_paths") or []) | set(item.get("evidence_paths") or []))
            current["platforms"] = {**(item.get("platforms") or {}), **(current.get("platforms") or {})}
    return best


def _map_readiness(value: Any, item: dict[str, Any], family: str) -> str:
    text = str(value or "").strip().lower()
    if "crypto_basis" in text:
        return READY_CRYPTO
    if "sports_operator" in text or "operator_scout" in text:
        return READY_SPORTS
    if "cdna_fill" in text:
        return READY_CDNA
    if "graph" in text:
        return READY_GRAPH
    if "recollection" in text or "not_found" in text:
        return NEEDS_RECOLLECTION
    if "reference" in text:
        return REFERENCE_ONLY
    if "needs_rule" in text or "rule" in text or "review" in text:
        if _family_group({"family": family, "category": item.get("category")}) == "crypto" and ("price_threshold" in family or "point" in family):
            return READY_CRYPTO
        return NEEDS_RULE
    platforms = _normalize_platforms(item.get("platforms") or item.get("platforms_found") or {})
    if _is_cdna_family(family, platforms):
        return READY_CDNA
    if _is_graph_family(family):
        return READY_GRAPH
    if _category_from_item(item, family) == "crypto" and platforms.get("kalshi") and platforms.get("polymarket"):
        return READY_CRYPTO
    if _category_from_item(item, family) == "sports" and (platforms.get("kalshi") or platforms.get("polymarket")):
        return READY_SPORTS
    return IGNORE_FOR_NOW


def _readiness_from_payload(payload: dict[str, Any], family: str) -> str:
    if payload.get("market_found") is False:
        return NEEDS_RECOLLECTION
    platform = str(payload.get("platform") or "").lower()
    category = _category_from_item(payload, family)
    if "crypto.com" in platform or "cdna" in platform:
        return READY_CDNA if payload.get("market_found") else REFERENCE_ONLY
    if category == "crypto":
        return READY_CRYPTO
    if category == "sports":
        return READY_SPORTS
    return NEEDS_RULE


def _category_from_item(item: dict[str, Any], family: str) -> str:
    category = str(item.get("category") or "").lower()
    if category:
        return category
    text = family.lower()
    if any(token in text for token in ("btc", "eth", "sol", "xrp", "doge", "crypto")):
        return "crypto"
    if any(token in text for token in ("mlb", "nba", "nhl", "nfl", "ucl", "open", "wnba", "ufc", "champion", "super_bowl", "division")):
        return "sports"
    if any(token in text for token in ("fed", "cpi", "unemployment", "payroll", "gdp")):
        return "economics"
    return "other"


def _normalize_platforms(platforms: dict[str, Any]) -> dict[str, bool]:
    out = {"kalshi": False, "polymarket": False, "cdna": False}
    for key, value in (platforms or {}).items():
        normalized = str(key).lower()
        if "kalshi" in normalized:
            out["kalshi"] = bool(value)
        elif "poly" in normalized:
            out["polymarket"] = bool(value)
        elif "cdna" in normalized or "crypto.com" in normalized:
            out["cdna"] = bool(value)
    return out


def _platforms_from_item(item: dict[str, Any]) -> dict[str, bool]:
    return {
        "kalshi": bool(item.get("kalshi")),
        "polymarket": bool(item.get("polymarket")),
        "cdna": bool(item.get("cdna")),
    }


def _evidence_paths_from_index(root: Path, item: dict[str, Any]) -> list[Path]:
    paths = []
    directory = item.get("dir") or item.get("key") or item.get("family")
    if directory:
        folder = root / str(directory)
        if folder.exists():
            paths.extend(folder.glob("*.json"))
    return paths


def _evidence_paths_from_family(root: Path, family: str) -> list[Path]:
    matches = list(root.rglob(f"{family}/*.json"))
    if matches:
        return matches
    return [path for path in root.rglob("*.json") if family in str(path).replace("\\", "/")]


def _family_key(value: Any) -> str:
    text = str(value or "unknown").strip().replace("\\", "/").split("/")[-1].lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "unknown"


def _family_group(row_or_family: Any) -> str:
    if isinstance(row_or_family, dict):
        category = str(row_or_family.get("category") or "").lower()
        family = str(row_or_family.get("family") or "").lower()
    else:
        category = ""
        family = str(row_or_family).lower()
    if category in {"crypto", "sports", "economics"}:
        return category
    if _category_from_item({}, family) in {"crypto", "sports", "economics"}:
        return _category_from_item({}, family)
    return "other"


def _priority(family: str, category: str, readiness: str) -> int:
    score = 0
    if family in CRYPTO_PRIORITY:
        score += CRYPTO_PRIORITY[family]
    elif family in SPORTS_PRIORITY:
        score += SPORTS_PRIORITY[family]
    elif category == "crypto":
        score += 50
    elif category == "sports":
        score += 35
    elif category == "economics":
        score += 20
    score += {
        READY_CRYPTO: 20,
        READY_SPORTS: 18,
        READY_CDNA: 16,
        READY_GRAPH: 12,
        NEEDS_RULE: 5,
        NEEDS_RECOLLECTION: -10,
        REFERENCE_ONLY: -15,
    }.get(readiness, 0)
    return score


def _source_score(root: Path, source_kind: str) -> int:
    text = str(root).lower()
    score = 0
    if "polished" in text:
        score += 100
    match = re.search(r"automation_batch_(\d+)", text)
    if match:
        score += int(match.group(1))
    if source_kind == "_index.jsonl":
        score += 10
    if source_kind == "import_readiness_matrix.json":
        score += 8
    return score


def _sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    return (-int(row.get("priority") or 0), -int(row.get("preferred_source_score") or 0), str(row.get("family")))


def _is_cdna_family(family: str, platforms: dict[str, bool]) -> bool:
    return platforms.get("cdna") or family.startswith("cdna_")


def _is_graph_family(family: str) -> bool:
    return any(token in family for token in ("division", "ucl", "french_open", "wnba", "neg_risk", "parlay", "basket"))


def _recommended_command(family: str, readiness: str) -> str:
    if readiness == READY_CRYPTO:
        asset = family.split("_", 1)[0].upper()
        return f"crypto-threshold-basis-review-scout --asset {asset} --kalshi-evidence <kalshi> --polymarket-evidence <polymarket>"
    if readiness == READY_SPORTS:
        return "championship-operator-scout-generic --family-folder <family-folder> --accept-operator-risk"
    if readiness == READY_CDNA:
        return "cdna-fill-first-scout --cdna-evidence <cdna> --partner-evidence <partner> --operator-accept-display-price-risk"
    if readiness == READY_GRAPH:
        return "structural-basket-parlay-scout --input-dir reports/manual_evidence"
    return "manual review"


def _top_tasks(crypto: list[dict[str, Any]], sports: list[dict[str, Any]], cdna: list[dict[str, Any]], graph: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks = []
    if crypto:
        row = crypto[0]
        tasks.append({"command": row.get("recommended_command"), "family": row.get("family"), "reason": "highest-priority crypto basis-risk family"})
    if len(crypto) > 1:
        row = crypto[1]
        tasks.append({"command": row.get("recommended_command"), "family": row.get("family"), "reason": "second crypto basis-risk target"})
    if sports:
        row = sports[0]
        tasks.append({"command": row.get("recommended_command"), "family": row.get("family"), "reason": "highest-priority sports operator family"})
    if cdna:
        row = cdna[0]
        tasks.append({"command": row.get("recommended_command"), "family": row.get("family"), "reason": "CDNA is fill-first/reference only"})
    if graph:
        row = graph[0]
        tasks.append({"command": row.get("recommended_command"), "family": row.get("family"), "reason": "structural graph review candidate"})
    return tasks[:10]


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    readiness = Counter(row.get("readiness_class") for row in rows)
    blockers = Counter()
    for row in rows:
        blockers.update(row.get("blockers") or [])
    summary = {
        "families": len(rows),
        "exact_ready_rows": 0,
        "paper_candidate_rows": 0,
        "top_blockers": [{"blocker": key, "count": value} for key, value in blockers.most_common(15)],
    }
    summary.update(readiness)
    return summary


def _row_md(idx: int, row: dict[str, Any]) -> str:
    platforms = ",".join(name for name, present in (row.get("platforms") or {}).items() if present) or "none"
    blockers = ", ".join((row.get("blockers") or [])[:4])
    evidence = (row.get("evidence_paths") or [""])[0]
    return f"| {idx} | {_md(row.get('family'))} | {_md(row.get('readiness_class'))} | {_md(platforms)} | {_md(blockers)} | {_md(evidence)} |"


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")

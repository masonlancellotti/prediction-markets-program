"""Daily phone-summary builder for the crypto arb system (reporting only).

Reads LOCAL report files only — micro-test final reports, structural trigger
reports, and the structural watch summary — and produces:

  * a machine-readable ``daily_summary.json`` (fixed schema);
  * a human ``daily_summary.md`` (full detail);
  * a concise ``daily_summary_message.txt`` (phone-sized, truncatable).

P&L policy is deliberately conservative: realized P&L is reported ONLY from
finalized micro-tests with known fills/settlement; otherwise an expected value is
reported when fills are known but the test is unsettled; when neither is known the
field is ``null`` and the message prints ``unknown`` — it never guesses. Account-
wide P&L is always ``not_available_from_local_reports`` (no account integration).

Delivery is delegated to :mod:`relative_value.notification_providers`; this module
adds NO trading, order, account, or browser code, and reads NO secrets itself.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from relative_value.notification_providers import NotificationProvider, make_provider

_FILLED_STATES = {"filled", "partially_filled", "partial", "filled_partial"}
_CANCEL_STATES = {"canceled", "cancelled", "cancelled_by_operator", "canceled_by_operator"}
_REJECT_STATES = {"rejected", "reject", "error", "errored"}


# --------------------------------------------------------------------------- #
# small helpers                                                               #
# --------------------------------------------------------------------------- #
def _f(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None, ndigits: int = 6) -> float | None:
    return None if value is None else round(value, ndigits)


def _norm_date(value: Any) -> str | None:
    """Normalize an ISO (``2026-05-30T..``) or compact (``20260530T..``) ts to ``YYYY-MM-DD``."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 (a malformed/locked report must not crash the summary)
        return None


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"${value:,.2f}"


def _date_matches(target: str | None, ts: Any) -> bool:
    """Include a report when no date filter is set, the report has no date, or they match."""
    if not target:
        return True
    nd = _norm_date(ts)
    return nd is None or nd == target


# --------------------------------------------------------------------------- #
# loaders                                                                     #
# --------------------------------------------------------------------------- #
def _load_finals(root: Path, date: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sub in ("crypto_micro_tests", "live_crypto_micro_tests"):
        base = root / sub
        if not base.exists():
            continue
        for fp in sorted(base.rglob("final_report.json")):
            data = _load_json(fp)
            if isinstance(data, dict) and _date_matches(date, data.get("finalized_at_utc")):
                out.append(data)
    return out


def _count_started(root: Path, date: str | None) -> int:
    started = 0
    base = root / "crypto_micro_tests"
    if base.exists():
        for fp in base.rglob("test_plan.json"):
            data = _load_json(fp)
            if isinstance(data, dict) and _date_matches(date, data.get("created_at_utc") or data.get("started_at_utc")):
                started += 1
    return started


def _load_triggers(root: Path, date: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not root.exists():
        return out
    seen: set[Path] = set()
    for pattern in ("crypto_structural_trigger", "crypto_fast_path*", "live_crypto_micro_tests"):
        for base in sorted(root.glob(pattern)):
            if not base.is_dir():
                continue
            for fp in sorted(base.rglob("trigger_report.json")):
                if fp in seen:
                    continue
                seen.add(fp)
                data = _load_json(fp)
                ts = (data or {}).get("detected_at_utc") or (data or {}).get("generated_at") or (data or {}).get("started_at")
                if isinstance(data, dict) and _date_matches(date, ts):
                    out.append(data)
    return out


def _latest_watch_summary(root: Path) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_key = ""
    for base in sorted(root.glob("crypto_structural_watch*")):
        fp = base / "watch_summary.json"
        if not fp.exists():
            continue
        data = _load_json(fp)
        if not isinstance(data, dict):
            continue
        key = str(data.get("updated_at") or "")
        if best is None or key > best_key:
            best, best_key = data, key
    return best


# --------------------------------------------------------------------------- #
# aggregation                                                                 #
# --------------------------------------------------------------------------- #
def build_daily_summary(*, reports_root: str | Path, date: str | None,
                        now: datetime | None = None) -> dict[str, Any]:
    root = Path(reports_root)
    gen = now or datetime.now(timezone.utc)
    warnings: list[str] = []

    finals = _load_finals(root, date)
    fills = canceled = rejected = trades = 0
    realized: float | None = None
    expected: float | None = None
    total_notional = 0.0
    residual = 0.0
    for fr in finals:
        leg_filled = 0
        for lr in fr.get("leg_results") or []:
            st = str(lr.get("order_status") or "").lower()
            if st in _FILLED_STATES:
                fills += 1
                leg_filled += 1
                fp, fq = _f(lr.get("filled_price")), _f(lr.get("filled_quantity"))
                if fp is not None and fq is not None:
                    total_notional += fp * fq
            elif st in _CANCEL_STATES:
                canceled += 1
            elif st in _REJECT_STATES:
                rejected += 1
        if leg_filled:
            trades += 1
        for r in fr.get("residual_exposure") or []:
            residual += _f(r.get("worst_case_loss_if_settles_zero")) or 0.0
        # --- conservative P&L ---
        matched = _f(fr.get("matched_basket_quantity")) or 0.0
        net = _f(fr.get("actual_net_edge_after_fees_if_all_filled"))
        if net is None:
            net = _f(fr.get("intended_net_edge_after_fees"))
        settled = str(fr.get("settlement_status") or "").lower().startswith("settled")
        explicit = _f(fr.get("realized_pnl"))
        if explicit is not None:
            realized = (realized or 0.0) + explicit
        elif settled and net is not None and matched > 0 and fr.get("guarantee_holds"):
            realized = (realized or 0.0) + net * matched
        elif net is not None and matched > 0:
            expected = (expected or 0.0) + net * matched

    if finals and realized is None:
        warnings.append("realized_pnl_not_available_from_local_reports")
    warnings.append("account_wide_pnl_not_available_from_local_reports")

    live_triggers = dry_run_triggers = 0
    for tr in _load_triggers(root, date):
        mode = str(tr.get("mode") or "").lower()
        n = int(tr.get("decisions") or tr.get("triggers_evaluated") or len(tr.get("triggers") or []) or 0)
        if mode == "live":
            live_triggers += n
        else:
            dry_run_triggers += n
        for d in tr.get("triggers") or []:
            for r in ((d.get("execution_result") or {}).get("residual_exposure") or []):
                residual += _f(r.get("worst_case_loss_if_settles_zero")) or 0.0
        if tr.get("emergency_review_required"):
            warnings.append("trigger_emergency_review_required")

    paper_found = 0
    best_edge: float | None = None
    top_blockers: list[dict[str, Any]] = []
    top_opps: list[dict[str, Any]] = []
    cdna_status: dict[str, Any] = {"supplied": False, "candidates": 0}
    ws = _latest_watch_summary(root)
    if ws:
        totals = ws.get("totals") or {}
        paper_found = int(totals.get("paper_candidates_found") or 0)
        best_edge = _f(totals.get("best_net_edge_after_fees"))
        top_blockers = [{"blocker": b.get("blocker"), "count": int(b.get("count") or 0)}
                        for b in (totals.get("top_actionable_buy_only_blockers") or [])[:5]]
        for r in (ws.get("paper_candidates") or ws.get("top_buy_only_near_misses") or [])[:5]:
            top_opps.append({"asset": r.get("asset"), "candidate_type": r.get("candidate_type"),
                             "net_edge_after_fees": _f(r.get("net_edge_after_fees")),
                             "dedup_key": r.get("dedup_key")})
        part = totals.get("cdna_participation") or {}
        gen_types = part.get("cdna_candidate_types_generated") or {}
        cdna_candidates = len(ws.get("cdna_fill_first_candidates") or []) or sum(int(v or 0) for v in gen_types.values())
        cdna_status = {"supplied": bool(part.get("cdna_supplied")), "candidates": int(cdna_candidates),
                       "candidate_types_generated": gen_types,
                       "missing_reason": part.get("cdna_missing_reason")}
        for err in (ws.get("latest_iteration_errors") or [])[:3]:
            warnings.append(f"watch:{err}")
        if date and _norm_date(ws.get("updated_at")) not in (None, date):
            warnings.append(f"watch_summary_is_from_{_norm_date(ws.get('updated_at'))}_not_{date}")

    return {
        "date": date,
        "generated_at_utc": gen.isoformat(),
        "trades_count": trades,
        "fills_count": fills,
        "canceled_count": canceled,
        "rejected_count": rejected,
        "realized_pnl": _round(realized, 4),
        "expected_pnl": _round(expected, 4),
        "total_notional": round(total_notional, 2),
        "open_residual_exposure": round(residual, 2),
        "paper_candidates_found": paper_found,
        "live_triggers": live_triggers,
        "dry_run_triggers": dry_run_triggers,
        "micro_tests_started": _count_started(root, date),
        "micro_tests_finalized": len(finals),
        "top_opportunities": top_opps,
        "top_blockers": top_blockers,
        "cdna_status": cdna_status,
        "warnings": warnings,
        # additive (not in the minimal schema, but useful):
        "best_net_edge_after_fees": _round(best_edge, 4),
        "account_wide_pnl": "not_available_from_local_reports",
        "sources_scanned": {"micro_test_final_reports": len(finals),
                            "watch_summary_present": ws is not None},
        "safety": {"reporting_only": True, "orders_or_execution_logic_added": False,
                   "auth_or_account_logic_added": False, "browser_automation_added": False,
                   "secrets_read_by_summary": False},
    }


# --------------------------------------------------------------------------- #
# rendering                                                                   #
# --------------------------------------------------------------------------- #
def _total_known_pnl(summary: Mapping[str, Any]) -> float | None:
    r, e = summary.get("realized_pnl"), summary.get("expected_pnl")
    if r is None and e is None:
        return None
    return round((r or 0.0) + (e or 0.0), 4)


def daily_summary_title(summary: Mapping[str, Any]) -> str:
    return f"Crypto Arb Daily {summary.get('date') or 'today'}"


def build_phone_message(summary: Mapping[str, Any], *, max_message_chars: int = 1500,
                        report_path: str = "") -> str:
    best = summary.get("best_net_edge_after_fees")
    best_line = "unknown" if best is None else f"{best * 100:.1f}% / ${best:.4f} per $1"
    lines = [
        daily_summary_title(summary),
        "",
        f"P&L: realized {_money(summary.get('realized_pnl'))} / expected {_money(summary.get('expected_pnl'))}"
        f" / total known {_money(_total_known_pnl(summary))}",
        f"Trades: {summary.get('fills_count', 0)} fills, {summary.get('canceled_count', 0)} canceled,"
        f" {summary.get('rejected_count', 0)} rejected",
        f"Best edge: {best_line}",
    ]
    message = "\n".join(lines)
    return _truncate(message, max_message_chars)


def _truncate(text: str, max_chars: int) -> str:
    if max_chars and max_chars > 0 and len(text) > max_chars:
        return text[: max(0, max_chars - 1)].rstrip() + "…"
    return text


def _rows_md(rows: list[dict[str, Any]], cols: list[tuple[str, str]]) -> str:
    if not rows:
        return "_none_\n"
    head = "| " + " | ".join(c[0] for c in cols) + " |\n"
    sep = "| " + " | ".join("---" for _ in cols) + " |\n"
    body = ""
    for r in rows:
        body += "| " + " | ".join(str(r.get(c[1], "")) for c in cols) + " |\n"
    return head + sep + body


def render_summary_markdown(summary: Mapping[str, Any]) -> str:
    cdna = summary.get("cdna_status") or {}
    out = [
        f"# {daily_summary_title(summary)}",
        "",
        f"_Generated {summary.get('generated_at_utc')} — reporting only, no trading._",
        "",
        "## P&L (conservative; local reports only)",
        f"- Realized: **{_money(summary.get('realized_pnl'))}** (only from finalized/settled tests with known fills)",
        f"- Expected / unrealized: **{_money(summary.get('expected_pnl'))}**",
        f"- Total known: **{_money(_total_known_pnl(summary))}**",
        f"- Account-wide P&L: _{summary.get('account_wide_pnl')}_",
        "",
        "## Activity",
        f"- Trades (tests with ≥1 fill): **{summary.get('trades_count', 0)}**",
        f"- Fills: **{summary.get('fills_count', 0)}** · Canceled: **{summary.get('canceled_count', 0)}**"
        f" · Rejected: **{summary.get('rejected_count', 0)}**",
        f"- Micro-tests started: **{summary.get('micro_tests_started', 0)}** · finalized: **{summary.get('micro_tests_finalized', 0)}**",
        f"- Total notional used: **{_money(summary.get('total_notional'))}**",
        f"- Open residual exposure (worst-case): **{_money(summary.get('open_residual_exposure'))}**",
        f"- Live triggers: **{summary.get('live_triggers', 0)}** · Dry-run triggers: **{summary.get('dry_run_triggers', 0)}**",
        "",
        "## Paper candidates",
        f"- Found: **{summary.get('paper_candidates_found', 0)}**",
        f"- Best net edge after fees: **{summary.get('best_net_edge_after_fees')}**",
        "",
        "### Top opportunities",
        _rows_md(list(summary.get("top_opportunities") or []),
                 [("Asset", "asset"), ("Type", "candidate_type"), ("Net edge", "net_edge_after_fees"), ("Key", "dedup_key")]),
        "### Top blockers",
        _rows_md(list(summary.get("top_blockers") or []), [("Blocker", "blocker"), ("Count", "count")]),
        "## CDNA participation",
        f"- Supplied: **{'yes' if cdna.get('supplied') else 'no'}** · candidates: **{cdna.get('candidates', 0)}**",
        f"- Types generated: `{cdna.get('candidate_types_generated') or {}}`",
        "",
        "## Safety warnings",
    ]
    warns = summary.get("warnings") or []
    out.extend([f"- {w}" for w in warns] or ["- _none_"])
    out += ["", "## Safety", f"```\n{json.dumps(summary.get('safety') or {}, indent=2)}\n```", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# orchestration                                                               #
# --------------------------------------------------------------------------- #
def write_and_send_daily_summary(
    *, reports_root: str | Path, date: str | None, provider_name: str = "dry_run", send: bool = False,
    json_output: str | Path, markdown_output: str | Path, message_output: str | Path,
    max_message_chars: int = 1500, env: Mapping[str, str] | None = None, http_post: Any = None,
    now: datetime | None = None, provider: NotificationProvider | None = None,
) -> dict[str, Any]:
    """Build the summary, write the three artifacts, and (only if ``send``) deliver."""
    summary = build_daily_summary(reports_root=reports_root, date=date, now=now)
    message = build_phone_message(summary, max_message_chars=max_message_chars, report_path=str(markdown_output))
    markdown = render_summary_markdown(summary)

    for path, content in ((markdown_output, markdown), (message_output, message)):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    prov = provider or make_provider(provider_name, env=env, http_post=http_post)
    result = prov.send(daily_summary_title(summary), message, allow_send=bool(send))

    # Persist a redacted delivery record alongside the summary (never secrets).
    summary["notification"] = {
        "requested_provider": provider_name, "send_flag": bool(send),
        "provider": result.get("provider"), "status": result.get("status"), "sent": result.get("sent"),
        "missing_env_vars": result.get("missing_env_vars"), "reason": result.get("reason"),
        "redacted_config": result.get("redacted_config"),
    }
    jp = Path(json_output)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    return {"summary": summary, "message": message, "markdown": markdown, "notification": result,
            "files": {"json": str(jp), "markdown": str(markdown_output), "message": str(message_output)}}

"""Economics and combined supplier verdicts for opportunity candidates."""

from __future__ import annotations

from typing import Any


def vat_rate(country_code: str, rules: Any) -> float:
    return {
        "FR": rules.vat_fr,
        "BE": rules.vat_be,
        "CH": rules.vat_ch,
        "LU": rules.vat_lu,
    }.get(country_code.upper(), rules.vat_fr)


def economics_for_candidate(
    candidate: dict[str, Any],
    *,
    market_price_ttc: float,
    country_code: str,
    rules: Any,
) -> dict[str, Any]:
    landed_cost = float(candidate.get("landed_cost_eur") or 0)
    vat = vat_rate(country_code, rules)
    revenue_ht = market_price_ttc / (1.0 + vat)
    gross_profit = revenue_ht - landed_cost
    margin = (gross_profit / revenue_ht * 100.0) if revenue_ht > 0 else 0.0

    if margin >= rules.min_margin_pct:
        unit_economics_verdict = "GO"
    elif margin >= 25.0:
        unit_economics_verdict = "WATCH"
    else:
        unit_economics_verdict = "NO_GO"

    quality_verdict = str(candidate.get("quality_verdict") or "WATCH")
    if unit_economics_verdict == "NO_GO":
        supplier_verdict = "NO_GO"
    elif unit_economics_verdict == "GO" and quality_verdict == "PASS":
        supplier_verdict = "GO"
    else:
        supplier_verdict = "WATCH"

    row = dict(candidate)
    row.update(
        {
            "market_price_ttc_eur": round(market_price_ttc, 2),
            "revenue_ht_eur": round(revenue_ht, 2),
            "gross_profit_eur": round(gross_profit, 2),
            "gross_margin_pct": round(margin, 1),
            "unit_economics_verdict": unit_economics_verdict,
            "supplier_verdict": supplier_verdict,
            # Backward-compatible field used by Product Factory status logic.
            # It is intentionally conservative: a quality-WATCH supplier cannot
            # become GO_CANDIDATE solely because the arithmetic margin is high.
            "economics_verdict": supplier_verdict,
        }
    )
    return row


def rank_candidate_economics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {"GO": 0, "WATCH": 1, "NO_GO": 2}
    return sorted(
        rows,
        key=lambda row: (
            order.get(str(row.get("supplier_verdict")), 9),
            -float(row.get("gross_margin_pct") or 0),
            -float(row.get("quality_score") or 0),
            -int(row.get("orders") or 0),
            float(row.get("landed_cost_eur") or 999999),
        ),
    )

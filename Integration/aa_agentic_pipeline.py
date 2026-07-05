"""
Akshaya Patra — Agentic Production Planning Pipeline (Automation Anywhere integration build)
----------------------------------------------------------------------------------------------
Reads directly from the single dataset workbook (Akshaya_Patra_Dataset.xlsx) — the actual
artifact you'd hand to a bot — instead of the intermediate JSON scaffolding. Runs the same
6-agent reasoning as production_planning_agent.py, but outputs in shapes an RPA tool can
consume directly:

  1. escalation_cards.csv      -> loop this in a bot (Excel/CSV package) to raise one
                                   approval task per row via AA's human-in-the-loop /
                                   work-queue feature.
  2. auto_resolved_actions.csv -> loop this to write audit-log entries / notifications,
                                   no human task needed.
  3. day_plan_output.json      -> same data as a single JSON payload, for a bot step that
                                   calls this via REST instead of reading files directly
                                   (see aa_rest_api.py for that wrapper).

Run:
    python3 aa_agentic_pipeline.py --dataset Akshaya_Patra_Dataset.xlsx --outdir ./output
"""

import argparse
import json
import os
import pandas as pd


TIER1 = "Tier 1 - Critical"


# --------------------------------------------------------------------------
# Load + clean
# --------------------------------------------------------------------------

def load_workbook(path):
    xl = pd.read_excel(path, sheet_name=None)
    programmes = xl["Programmes"]
    centres = xl["Centres"]
    supplier_hist = xl["Supplier_Delivery_History"]
    decision_programme = xl["Decision_Log_Programme"]
    decision_aggregate = xl["Decision_Log_Aggregate"]
    planning = xl["Planning_Report_Day4"]
    inventory = xl["Inventory_Day4"]

    # Decision_Log_Programme has a free-text timeline block appended below the
    # real table (see dataset design) — keep only rows with a valid programme_id.
    decision_programme = decision_programme[
        decision_programme["programme_id"].astype(str).str.match(r"^PRG-\d+$", na=False)
    ].copy()

    return {
        "programmes": programmes,
        "centres": centres,
        "supplier_hist": supplier_hist,
        "decision_programme": decision_programme,
        "decision_aggregate": decision_aggregate,
        "planning": planning,
        "inventory": inventory,
    }


# --------------------------------------------------------------------------
# Agent 2 — Supply Reliability (computed fresh per requirement, not read
# from the pre-filled Supplier_Reliability_Summary sheet, so the score
# reflects TODAY's requested volume, not a generic historical average)
# --------------------------------------------------------------------------

def supplier_reliability(supplier_hist, supplier_id, today_required_kg):
    hist = supplier_hist[supplier_hist["supplier_id"] == supplier_id]
    if hist.empty:
        return None
    ratios = hist["delivered_kg"] / hist["committed_kg"]
    naive = ratios.mean()

    high_load = hist[hist["committed_kg"] >= 0.9 * today_required_kg]
    if not high_load.empty:
        pressure_adjusted = (high_load["delivered_kg"] / high_load["committed_kg"]).mean()
        tested = True
    else:
        pressure_adjusted = naive
        tested = False

    return {
        "supplier_id": supplier_id,
        "supplier_name": hist["supplier_name"].iloc[0],
        "naive_score": round(naive, 3),
        "pressure_adjusted_score": round(pressure_adjusted, 3),
        "tested_at_this_load": tested,
        "risk": bool(pressure_adjusted < 0.6),
    }


# --------------------------------------------------------------------------
# Agent 3 — Programme Priority / silent deprioritisation
# --------------------------------------------------------------------------

def detect_silent_deprioritisation(decision_programme, decision_aggregate, programmes):
    tier1_ids = set(programmes.loc[programmes["criticality_tier"] == TIER1, "programme_id"])
    df = decision_programme[decision_programme["programme_id"].isin(tier1_ids)].copy()
    df["ratio"] = df["actual_meals"] / df["planned_meals"]

    findings = []
    for (centre_id, programme_id), grp in df.groupby(["centre_id", "programme_id"]):
        if len(grp) < 2:
            continue  # single low cycle is an acute event, not a hidden multi-cycle pattern
        avg_ratio = grp["ratio"].mean()
        avg_util = decision_aggregate.loc[
            decision_aggregate["centre_id"] == centre_id, "capacity_utilisation"
        ].mean()
        if avg_ratio < 0.85 and avg_util >= 0.95:
            findings.append({
                "type": "SILENT_DEPRIORITISATION",
                "centre_id": centre_id,
                "programme_id": programme_id,
                "cycle_ratios": [round(r, 3) for r in grp["ratio"].tolist()],
                "avg_fulfilment": round(avg_ratio, 3),
                "avg_aggregate_utilisation": round(avg_util, 3),
                "explanation": (
                    f"Centre {centre_id} averaged {avg_util:.1%} of aggregate target across "
                    f"cycles while Tier-1 programme {programme_id} averaged only "
                    f"{avg_ratio:.1%} fulfilment. The aggregate number conceals this."
                ),
            })
    return findings


# --------------------------------------------------------------------------
# Agent 4 — Capacity & Load
# --------------------------------------------------------------------------

def capacity_analysis(centres, decision_aggregate, planning):
    # Planning_Report_Day4 is flattened one-row-per-ingredient, so a programme
    # with 2+ tracked ingredients would otherwise have its meals_required
    # counted once per ingredient row. Dedupe to one row per (centre, programme)
    # before summing — this is exactly the kind of silent aggregation error
    # the pipeline exists to catch, so it needs to not make the same mistake.
    programme_level = planning.drop_duplicates(subset=["centre_id", "programme_id"])
    today_load = programme_level.groupby("centre_id")["meals_required"].sum()
    hist_util = decision_aggregate.groupby("centre_id")["capacity_utilisation"].mean()

    results = []
    for _, c in centres.iterrows():
        centre_id = c["centre_id"]
        capacity = c["base_daily_capacity_meals"]
        required = int(today_load.get(centre_id, 0))
        forecast_util = required / capacity if capacity else 0
        results.append({
            "centre_id": centre_id,
            "required_meals": required,
            "capacity": int(capacity),
            "forecast_utilisation": round(forecast_util, 3),
            "historical_avg_utilisation": (
                round(hist_util[centre_id], 3) if centre_id in hist_util.index else None
            ),
            "over_capacity_by_meals": max(0, required - capacity),
        })
    return results


def find_slack_centre(analysis, exclude, risk_flagged_centres):
    candidates = [
        r for r in analysis
        if r["centre_id"] != exclude
        and r["forecast_utilisation"] < 0.9
        and r["centre_id"] not in risk_flagged_centres
    ]
    candidates.sort(key=lambda r: -(r["historical_avg_utilisation"] or 0))
    return candidates[0] if candidates else None


# --------------------------------------------------------------------------
# Agent 5 — Cascade Risk & Dependency
# --------------------------------------------------------------------------

def supply_risk_claims(planning, supplier_hist, programmes):
    claims = []
    prog_tier = dict(zip(programmes["programme_id"], programmes["criticality_tier"]))
    for _, r in planning.iterrows():
        sid = r.get("supplier_id")
        if pd.isna(sid):
            continue
        score = supplier_reliability(supplier_hist, sid, r["kg_required"])
        if score and score["risk"]:
            tier = prog_tier.get(r["programme_id"], "Unknown")
            claims.append({
                "type": "SUPPLY_RISK",
                "centre_id": r["centre_id"],
                "programme_id": r["programme_id"],
                "programme_tier": tier,
                "ingredient": r["ingredient_item"],
                "supplier": score,
                "meals_at_risk": int(r["meals_required"]),
                "explanation": (
                    f"{r['ingredient_item']} for programme {r['programme_id']} ({tier}) at "
                    f"{r['centre_id']} depends on {score['supplier_name']}, whose "
                    f"pressure-adjusted reliability at this volume is "
                    f"{score['pressure_adjusted_score']:.0%} (naive average looks like "
                    f"{score['naive_score']:.0%})."
                ),
            })
    return claims


def allocation_conflict_claims(planning, inventory, programmes):
    prog_tier = dict(zip(programmes["programme_id"], programmes["criticality_tier"]))
    claims = []
    for (centre_id, item), grp in planning.dropna(subset=["ingredient_item"]).groupby(
        ["centre_id", "ingredient_item"]
    ):
        if len(grp) < 2:
            continue
        claimants = [
            {"programme_id": row["programme_id"], "kg_required": row["kg_required"],
             "tier": prog_tier.get(row["programme_id"], "Unknown")}
            for _, row in grp.iterrows()
        ]
        total_claimed = grp["kg_required"].sum()

        inv_rows = inventory[(inventory["centre_id"] == centre_id) & (inventory["item"] == item)]
        available = 0
        if not inv_rows.empty:
            on_hand = inv_rows["on_hand_kg"].dropna().sum()
            incoming = inv_rows["incoming_expected_kg"].dropna().sum()
            available = on_hand + incoming

        resolvable = available >= total_claimed
        claims.append({
            "type": "ALLOCATION_CONFLICT" if not resolvable else "ALLOCATION_AUTO_RESOLVED",
            "centre_id": centre_id,
            "item": item,
            "claimants": claimants,
            "total_claimed_kg": float(total_claimed),
            "available_kg": float(available),
            "resolvable_from_supply": bool(resolvable),
            "reservations": (
                {c["programme_id"]: c["kg_required"] for c in claimants} if resolvable else None
            ),
            "explanation": (
                f"{len(claimants)} programmes at {centre_id} draw from the same stock pool "
                f"'{item}' ({total_claimed:.0f}kg claimed vs {available:.0f}kg available). "
                + ("Supply covers both claims — reservations locked per programme before "
                   "production starts." if resolvable else
                   "Supply is INSUFFICIENT for both full claims — requires a human priority call.")
            ),
        })
    return claims


# --------------------------------------------------------------------------
# Orchestrator
# --------------------------------------------------------------------------

def build_plan(priority_findings, capacity_rows, cascade_claims):
    auto_resolved, escalations = [], []

    risk_flagged = {f["centre_id"] for f in priority_findings} | {
        c["centre_id"] for c in cascade_claims if c["type"] in ("SUPPLY_RISK", "ALLOCATION_CONFLICT")
    }

    for f in priority_findings:
        escalations.append({
            "priority": "HIGH", "reason": f["type"], "centre_id": f["centre_id"],
            "programme_id": f["programme_id"], "explanation": f["explanation"],
            "recommended_action": (
                "Review shared-equipment/line allocation policy for this centre; current "
                "default is starving a Tier 1 programme across multiple cycles."
            ),
        })

    for c in capacity_rows:
        if c["over_capacity_by_meals"] > 0:
            slack = find_slack_centre(capacity_rows, exclude=c["centre_id"], risk_flagged_centres=risk_flagged)
            action = (
                f"Shift ~{c['over_capacity_by_meals']} meals of steady-state load to "
                f"{slack['centre_id']} (forecast utilisation {slack['forecast_utilisation']:.0%})"
                if slack else
                "No centre currently has enough slack — escalate to Regional Manager."
            )
            escalations.append({
                "priority": "HIGH", "reason": "CAPACITY_CEILING_BREACH", "centre_id": c["centre_id"],
                "programme_id": "", "explanation": (
                    f"{c['required_meals']} meals required vs {c['capacity']} capacity "
                    f"({c['forecast_utilisation']:.0%} of ceiling)."
                ),
                "recommended_action": action,
            })

    for claim in cascade_claims:
        if claim["type"] == "SUPPLY_RISK":
            is_tier1 = claim["programme_tier"] == TIER1
            escalations.append({
                "priority": "HIGH" if is_tier1 else "MEDIUM", "reason": "SUPPLY_RISK",
                "centre_id": claim["centre_id"], "programme_id": claim["programme_id"],
                "explanation": claim["explanation"],
                "recommended_action": (
                    "Pre-emptively source from a backup supplier or reduce today's committed "
                    "volume now — do not wait for the delivery window." if is_tier1 else
                    "Monitor delivery window; lower-tier programme has more slack to absorb a delay."
                ),
            })
        elif claim["type"] == "ALLOCATION_CONFLICT":
            escalations.append({
                "priority": "HIGH", "reason": "ALLOCATION_CONFLICT_INSUFFICIENT_SUPPLY",
                "centre_id": claim["centre_id"], "programme_id": "",
                "explanation": claim["explanation"],
                "recommended_action": "Requires a human priority call between competing Tier-1 claims.",
            })
        elif claim["type"] == "ALLOCATION_AUTO_RESOLVED":
            auto_resolved.append({
                "action": "LOCK_PER_PROGRAMME_RESERVATION", "centre_id": claim["centre_id"],
                "item": claim["item"], "explanation": claim["explanation"],
            })

    return auto_resolved, escalations


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def run(dataset_path):
    data = load_workbook(dataset_path)
    priority_findings = detect_silent_deprioritisation(
        data["decision_programme"], data["decision_aggregate"], data["programmes"]
    )
    capacity_rows = capacity_analysis(data["centres"], data["decision_aggregate"], data["planning"])
    cascade_claims = (
        supply_risk_claims(data["planning"], data["supplier_hist"], data["programmes"])
        + allocation_conflict_claims(data["planning"], data["inventory"], data["programmes"])
    )
    auto_resolved, escalations = build_plan(priority_findings, capacity_rows, cascade_claims)
    return {
        "capacity_analysis": capacity_rows,
        "priority_findings": priority_findings,
        "cascade_claims": cascade_claims,
        "auto_resolved_actions": auto_resolved,
        "escalation_cards": escalations,
    }


def write_outputs(result, outdir):
    os.makedirs(outdir, exist_ok=True)

    pd.DataFrame(result["escalation_cards"]).to_csv(
        os.path.join(outdir, "escalation_cards.csv"), index=False
    )
    pd.DataFrame(result["auto_resolved_actions"]).to_csv(
        os.path.join(outdir, "auto_resolved_actions.csv"), index=False
    )
    with open(os.path.join(outdir, "day_plan_output.json"), "w") as f:
        json.dump(result, f, indent=2, default=str)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="Akshaya_Patra_Dataset.xlsx")
    parser.add_argument("--outdir", default="./output")
    args = parser.parse_args()

    result = run(args.dataset)
    write_outputs(result, args.outdir)

    print(f"Escalation cards: {len(result['escalation_cards'])}")
    for e in result["escalation_cards"]:
        print(f"  [{e['priority']}] {e['reason']} — {e['centre_id']} {e['programme_id']}")
    print(f"Auto-resolved actions: {len(result['auto_resolved_actions'])}")
    print(f"\nOutputs written to {args.outdir}/ "
          f"(escalation_cards.csv, auto_resolved_actions.csv, day_plan_output.json)")

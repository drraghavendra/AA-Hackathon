"""
Akshaya Patra — Agentic Production Planning Prototype
------------------------------------------------------
Implements the 6-agent + orchestrator pipeline described in
Akshaya_Patra_Agentic_Workflow_Design.md, running against the dataset
in ./dataset/. Every score below is COMPUTED from raw history, not
copied from the dataset's illustrative pre-filled fields — this is the
part that proves the reasoning actually works, not just that the JSON
was written correctly.

Run:
    python3 production_planning_agent.py --dataset ./dataset --out day4_plan_output.json
"""

import json
import os
import argparse
from collections import defaultdict


# --------------------------------------------------------------------------
# Data loading
# --------------------------------------------------------------------------

def load_dataset(path):
    def j(name):
        with open(os.path.join(path, name)) as f:
            return json.load(f)
    return {
        "programmes": {p["programme_id"]: p for p in j("01_programmes.json")["programmes"]},
        "centres": {c["centre_id"]: c for c in j("02_centres.json")["centres"]},
        "suppliers": {s["supplier_id"]: s for s in j("03_suppliers_reliability_history.json")["suppliers"]},
        "decision_log": j("04_decision_log_cycles1-3.json")["decision_log"],
        "report": j("05_planning_report_day4.json")["requirements"],
        "inventory": j("06_inventory_state_day4.json")["inventory"],
    }


# --------------------------------------------------------------------------
# Agent 2 — Supply Reliability Agent
# --------------------------------------------------------------------------

class SupplyReliabilityAgent:
    """Computes naive vs pressure-adjusted reliability per supplier,
    conditioned on whether today's required volume matches a 'high load'
    profile the supplier has (or hasn't) been tested against."""

    def __init__(self, suppliers):
        self.suppliers = suppliers

    def score(self, supplier_id, today_required_kg):
        sup = self.suppliers.get(supplier_id)
        if not sup:
            return None
        history = sup["history"]
        ratios = [h["delivered_kg"] / h["committed_kg"] for h in history]
        naive = sum(ratios) / len(ratios)

        # A cycle counts as "matching today's load" if its committed volume
        # was at least 90% of what's being asked today — i.e. it actually
        # tested the supplier near this volume, rather than well below it.
        high_load_ratios = [
            h["delivered_kg"] / h["committed_kg"]
            for h in history
            if h["committed_kg"] >= 0.9 * today_required_kg
        ]
        pressure_adjusted = (sum(high_load_ratios) / len(high_load_ratios)
                             if high_load_ratios else naive)
        tested_at_this_load = bool(high_load_ratios)

        return {
            "supplier_id": supplier_id,
            "supplier_name": sup["name"],
            "naive_score": round(naive, 3),
            "pressure_adjusted_score": round(pressure_adjusted, 3),
            "tested_at_this_load": tested_at_this_load,
            "risk": pressure_adjusted < 0.6,
        }


# --------------------------------------------------------------------------
# Agent 3 — Programme Priority Agent
# --------------------------------------------------------------------------

class ProgrammePriorityAgent:
    """Re-ranks by criticality tier and detects multi-cycle silent
    deprioritisation: a Tier 1 programme trending down while the
    centre's aggregate output looks healthy."""

    def __init__(self, programmes, decision_log):
        self.programmes = programmes
        self.decision_log = decision_log

    def tier(self, programme_id):
        return self.programmes[programme_id]["criticality_tier"]

    def detect_silent_deprioritisation(self):
        findings = []
        by_centre_programme = defaultdict(list)
        agg_utilisation = defaultdict(list)

        for entry in self.decision_log:
            agg_utilisation[entry["centre_id"]].append(entry.get("capacity_utilisation", 0))
            for pb in entry.get("programme_breakdown", []):
                by_centre_programme[(entry["centre_id"], pb["programme_id"])].append(
                    pb["actual"] / pb["planned"]
                )

        for (centre_id, programme_id), ratios in by_centre_programme.items():
            if self.programmes[programme_id]["criticality_tier"] != "Tier 1 - Critical":
                continue
            if len(ratios) < 2:
                # A single low cycle is an acute event (handled by the
                # Cascade/Supply-Risk agent if it traces to a supply cause),
                # not the multi-cycle "hidden behind a healthy aggregate"
                # pattern this check exists to catch.
                continue
            avg_ratio = sum(ratios) / len(ratios)
            avg_util = sum(agg_utilisation[centre_id]) / len(agg_utilisation[centre_id])
            if avg_ratio < 0.85 and avg_util >= 0.95:
                findings.append({
                    "type": "SILENT_DEPRIORITISATION",
                    "centre_id": centre_id,
                    "programme_id": programme_id,
                    "cycle_ratios": [round(r, 3) for r in ratios],
                    "avg_fulfilment": round(avg_ratio, 3),
                    "avg_aggregate_utilisation": round(avg_util, 3),
                    "explanation": (
                        f"Centre {centre_id} averaged {avg_util:.1%} of aggregate target "
                        f"across cycles while Tier-1 programme {programme_id} averaged only "
                        f"{avg_ratio:.1%} fulfilment. The aggregate number conceals this."
                    ),
                })
        return findings


# --------------------------------------------------------------------------
# Agent 4 — Capacity & Load Agent
# --------------------------------------------------------------------------

class CapacityLoadAgent:
    """Cross-centre view: today's committed load vs physical ceiling,
    and which centres have historical slack available to absorb load."""

    def __init__(self, centres, decision_log, report):
        self.centres = centres
        self.decision_log = decision_log
        self.report = report

    def historical_utilisation(self, centre_id):
        vals = [e["capacity_utilisation"] for e in self.decision_log if e["centre_id"] == centre_id]
        return sum(vals) / len(vals) if vals else None

    def today_load(self):
        load = defaultdict(int)
        for r in self.report:
            load[r["centre_id"]] += r["meals_required"]
        return load

    def analyse(self):
        load = self.today_load()
        results = []
        for centre_id, capacity in ((cid, c["base_daily_capacity_meals"]) for cid, c in self.centres.items()):
            required = load.get(centre_id, 0)
            forecast_util = required / capacity if capacity else 0
            results.append({
                "centre_id": centre_id,
                "required_meals": required,
                "capacity": capacity,
                "forecast_utilisation": round(forecast_util, 3),
                "historical_avg_utilisation": (
                    round(hist_util, 3) if (hist_util := self.historical_utilisation(centre_id)) is not None else None
                ),
                "over_capacity_by_meals": max(0, required - capacity),
            })
        return results

    def find_slack_centre(self, analysis, exclude, risk_flagged_centres=None):
        risk_flagged_centres = risk_flagged_centres or set()
        candidates = [
            r for r in analysis
            if r["centre_id"] != exclude
            and r["forecast_utilisation"] < 0.9
            and r["centre_id"] not in risk_flagged_centres
        ]
        # Prefer the centre with the strongest proven historical record
        # (not just whoever looks emptiest today) - an empty-looking centre
        # that's empty because it's carrying its own live risk isn't a safe
        # place to route more load.
        candidates.sort(key=lambda r: (-(r["historical_avg_utilisation"] or 0)))
        return candidates[0] if candidates else None


# --------------------------------------------------------------------------
# Agent 5 — Cascade Risk & Dependency Agent
# --------------------------------------------------------------------------

class CascadeRiskAgent:
    """Walks ingredient -> programme -> centre, propagating supply risk
    onto criticality tier, and separately checks for uncoordinated
    multi-programme claims on one shared stock pool (the C4 failure mode)."""

    def __init__(self, report, inventory, programmes, supply_agent):
        self.report = report
        self.inventory = {(i["centre_id"], i["item"]): i for i in inventory}
        self.programmes = programmes
        self.supply_agent = supply_agent

    def supply_risk_claims(self):
        claims = []
        for r in self.report:
            for ing in r.get("key_ingredients", []):
                sid = ing.get("supplier_id")
                if not sid:
                    continue
                score = self.supply_agent.score(sid, ing["kg_required"])
                if score and score["risk"]:
                    programme = self.programmes[r["programme_id"]]
                    claims.append({
                        "type": "SUPPLY_RISK",
                        "centre_id": r["centre_id"],
                        "programme_id": r["programme_id"],
                        "programme_tier": programme["criticality_tier"],
                        "ingredient": ing["item"],
                        "supplier": score,
                        "meals_at_risk": r["meals_required"],
                        "explanation": (
                            f"{ing['item']} for programme {r['programme_id']} "
                            f"({programme['criticality_tier']}) at {r['centre_id']} depends on "
                            f"{score['supplier_name']}, whose pressure-adjusted reliability at "
                            f"this volume is {score['pressure_adjusted_score']:.0%} "
                            f"(naive average looks like {score['naive_score']:.0%})."
                        ),
                    })
        return claims

    def allocation_conflict_claims(self):
        claims = []
        pool = defaultdict(list)
        for r in self.report:
            for ing in r.get("key_ingredients", []):
                key = (r["centre_id"], ing["item"])
                pool[key].append({
                    "programme_id": r["programme_id"],
                    "kg_required": ing["kg_required"],
                    "tier": self.programmes[r["programme_id"]]["criticality_tier"],
                })

        for (centre_id, item), claimants in pool.items():
            if len(claimants) < 2:
                continue  # no shared claim, nothing to reconcile
            total_claimed = sum(c["kg_required"] for c in claimants)
            inv = self.inventory.get((centre_id, item))
            available = 0
            if inv:
                available = inv.get("on_hand_kg", 0) + sum(
                    d.get("expected_kg", 0) for d in inv.get("incoming", [])
                )
            resolvable = available >= total_claimed
            claims.append({
                "type": "ALLOCATION_CONFLICT" if not resolvable else "ALLOCATION_AUTO_RESOLVED",
                "centre_id": centre_id,
                "item": item,
                "claimants": claimants,
                "total_claimed_kg": total_claimed,
                "available_kg": available,
                "resolvable_from_supply": resolvable,
                "reservations": (
                    {c["programme_id"]: c["kg_required"] for c in claimants} if resolvable else None
                ),
                "explanation": (
                    f"{len(claimants)} programmes at {centre_id} draw from the same stock pool "
                    f"'{item}' ({total_claimed}kg claimed vs {available}kg available). "
                    + ("Supply covers both claims — reservations locked per programme before "
                       "production starts, closing the gap that previously caused a mid-cycle "
                       "reversal." if resolvable else
                       "Supply is INSUFFICIENT for both full claims — this requires a human "
                       "priority call between programmes.")
                ),
            })
        return claims


# --------------------------------------------------------------------------
# Agent 6 — Orchestrator
# --------------------------------------------------------------------------

class Orchestrator:
    TIER1 = "Tier 1 - Critical"

    def build_plan(self, priority_findings, capacity_analysis, cascade_claims, programmes, decision_log_agent):
        auto_resolved = []
        escalations = []

        risk_flagged_centres = {f["centre_id"] for f in priority_findings} | {
            c["centre_id"] for c in cascade_claims if c["type"] in ("SUPPLY_RISK", "ALLOCATION_CONFLICT")
        }

        # Silent deprioritisation is always a human/policy call
        for f in priority_findings:
            escalations.append({
                "priority": "HIGH",
                "reason": f["type"],
                "detail": f,
                "recommended_action": (
                    "Review shared-equipment/line allocation policy for this centre; "
                    "current default is starving a Tier 1 programme across multiple cycles."
                ),
            })

        # Capacity overruns -> propose reallocation, always human sign-off since it
        # reroutes a committed programme's production source
        for c in capacity_analysis:
            if c["over_capacity_by_meals"] > 0:
                slack = decision_log_agent.find_slack_centre(
                    capacity_analysis, exclude=c["centre_id"], risk_flagged_centres=risk_flagged_centres
                )
                escalations.append({
                    "priority": "HIGH",
                    "reason": "CAPACITY_CEILING_BREACH",
                    "detail": c,
                    "recommended_action": (
                        f"Shift ~{c['over_capacity_by_meals']} meals of steady-state load to "
                        f"{slack['centre_id']} (forecast utilisation {slack['forecast_utilisation']:.0%})"
                        if slack else
                        "No centre currently has enough slack — escalate to Regional Manager for "
                        "either a grace period or emergency capacity."
                    ),
                })

        # Cascade claims
        for claim in cascade_claims:
            if claim["type"] == "SUPPLY_RISK":
                is_tier1 = claim["programme_tier"] == self.TIER1
                escalations.append({
                    "priority": "HIGH" if is_tier1 else "MEDIUM",
                    "reason": "SUPPLY_RISK",
                    "detail": claim,
                    "recommended_action": (
                        "Pre-emptively source from a backup supplier or reduce today's committed "
                        "volume now and communicate proactively — do not wait for the delivery window."
                        if is_tier1 else
                        "Monitor delivery window; lower-tier programme has more slack to absorb a delay."
                    ),
                })
            elif claim["type"] == "ALLOCATION_CONFLICT":
                escalations.append({
                    "priority": "HIGH",
                    "reason": "ALLOCATION_CONFLICT_INSUFFICIENT_SUPPLY",
                    "detail": claim,
                    "recommended_action": "Requires a human priority call between competing Tier-1 claims.",
                })
            elif claim["type"] == "ALLOCATION_AUTO_RESOLVED":
                auto_resolved.append({
                    "action": "LOCK_PER_PROGRAMME_RESERVATION",
                    "detail": claim,
                })

        return {"auto_resolved_actions": auto_resolved, "escalation_cards": escalations}


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def run(dataset_path):
    data = load_dataset(dataset_path)

    supply_agent = SupplyReliabilityAgent(data["suppliers"])
    priority_agent = ProgrammePriorityAgent(data["programmes"], data["decision_log"])
    capacity_agent = CapacityLoadAgent(data["centres"], data["decision_log"], data["report"])
    cascade_agent = CascadeRiskAgent(data["report"], data["inventory"], data["programmes"], supply_agent)
    orchestrator = Orchestrator()

    priority_findings = priority_agent.detect_silent_deprioritisation()
    capacity_analysis = capacity_agent.analyse()
    cascade_claims = cascade_agent.supply_risk_claims() + cascade_agent.allocation_conflict_claims()

    plan = orchestrator.build_plan(
        priority_findings, capacity_analysis, cascade_claims, data["programmes"], capacity_agent
    )

    return {
        "capacity_analysis": capacity_analysis,
        "priority_findings": priority_findings,
        "cascade_claims": cascade_claims,
        "day_plan": plan,
    }


def print_summary(result):
    print("=" * 78)
    print("CAPACITY ANALYSIS (today's load vs physical ceiling)")
    print("=" * 78)
    for c in result["capacity_analysis"]:
        flag = " <<< OVER CAPACITY" if c["over_capacity_by_meals"] > 0 else ""
        hist = f"{c['historical_avg_utilisation']:.0%}" if c["historical_avg_utilisation"] is not None else "no history (novel demand)"
        print(f"  {c['centre_id']}: {c['required_meals']:>6} meals required / "
              f"{c['capacity']:>6} capacity  (forecast {c['forecast_utilisation']:.0%}, "
              f"historical avg {hist}){flag}")

    print("\n" + "=" * 78)
    print("DAY PLAN — AUTO-RESOLVED ACTIONS")
    print("=" * 78)
    for a in result["day_plan"]["auto_resolved_actions"]:
        print(f"  [AUTO] {a['action']}: {a['detail']['explanation']}")

    print("\n" + "=" * 78)
    print("DAY PLAN — ESCALATION CARDS (human decision required)")
    print("=" * 78)
    for e in result["day_plan"]["escalation_cards"]:
        print(f"\n  [{e['priority']}] {e['reason']}")
        detail = e["detail"]
        print(f"    {detail.get('explanation', detail)}")
        print(f"    -> Recommended: {e['recommended_action']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="./dataset")
    parser.add_argument("--out", default="day4_plan_output.json")
    args = parser.parse_args()

    result = run(args.dataset)
    print_summary(result)

    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nFull structured output written to {args.out}")

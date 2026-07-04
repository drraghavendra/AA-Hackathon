"""
Akshaya Patra Agentic Production Planning — Reference Prototype
=================================================================

This is a runnable simulation of the multi-agent reasoning pipeline described in
Akshaya_Patra_Agentic_Workflow_Design.md. It reads the same dataset (dataset/*.json),
runs the six specialised agents + orchestrator, and prints the final day plan:
auto-resolved actions, escalation cards for human decision points, and the
reasoning trail behind each.

This is intentionally dependency-free (pure Python 3, standard library only) so it
runs anywhere without setup — useful for a hackathon demo. It is a reference
implementation of the REASONING LOGIC, not a production system: in a real deployment,
the data loaders below would be replaced by live connectors (ERP/inventory system,
supplier portal, centre telemetry) feeding the same agent interfaces.

Usage:
    python agentic_workflow.py
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset")


def load(filename: str) -> Any:
    with open(os.path.join(DATA_DIR, filename)) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class DataStore:
    """Loads and indexes all reference data. In production this class is the
    seam where live system connectors would replace static JSON reads."""

    def __init__(self, data_dir: str = DATA_DIR):
        self.programmes: Dict[str, dict] = {
            p["programme_id"]: p for p in load("01_programmes.json")["programmes"]
        }
        self.centres: Dict[str, dict] = {
            c["centre_id"]: c for c in load("02_centres.json")["centres"]
        }
        self.suppliers: Dict[str, dict] = {
            s["supplier_id"]: s for s in load("03_suppliers_reliability_history.json")["suppliers"]
        }
        self.decision_log: List[dict] = load("04_decision_log_cycles1-3.json")["decision_log"]
        self.today_requirements: List[dict] = load("05_planning_report_day4.json")["requirements"]
        self.today_inventory: List[dict] = load("06_inventory_state_day4.json")["inventory"]

    def inventory_for(self, centre_id: str, item: str) -> Optional[dict]:
        for row in self.today_inventory:
            if row["centre_id"] == centre_id and row["item"] == item:
                return row
        return None

    def historical_utilisation(self, centre_id: str, clean_only: bool = True) -> List[float]:
        """By default only counts cycles with no flags raised — a cycle where a
        centre under-produced *because* of a supply shortfall or reversal isn't
        real spare capacity, it's a symptom, and shouldn't be read as slack."""
        rows = [
            entry for entry in self.decision_log
            if entry["centre_id"] == centre_id and "capacity_utilisation" in entry
        ]
        if clean_only:
            clean = [r for r in rows if not r.get("flags")]
            if clean:
                rows = clean
        return [r["capacity_utilisation"] for r in rows]


# ---------------------------------------------------------------------------
# Shared claim object — every agent emits these; the orchestrator reconciles them
# ---------------------------------------------------------------------------

@dataclass
class Claim:
    source_agent: str
    severity: str          # "info" | "risk" | "critical"
    centre_id: str
    programme_id: Optional[str]
    summary: str
    detail: str
    recommended_options: List[str] = field(default_factory=list)
    auto_resolvable: bool = False
    auto_action: Optional[str] = None


# ---------------------------------------------------------------------------
# Agent 2 — Supply Reliability
# ---------------------------------------------------------------------------

class SupplyReliabilityAgent:
    """Scores suppliers on naive vs pressure-adjusted reliability, and flags
    any today's-ask that exceeds what a supplier has ever cleanly delivered
    under a comparable load condition."""

    def __init__(self, store: DataStore):
        self.store = store

    def run(self) -> List[Claim]:
        claims = []
        for req in self.store.today_requirements:
            for ing in req.get("key_ingredients", []):
                supplier_id = ing.get("supplier_id")
                if not supplier_id:
                    continue
                supplier = self.store.suppliers.get(supplier_id)
                if not supplier:
                    continue

                pressure_score = supplier.get("pressure_adjusted_score", 1.0)
                naive_score = supplier.get("naive_reliability_score", 1.0)
                max_clean_delivery = max(
                    (h["delivered_kg"] for h in supplier["history"] if h.get("on_time")),
                    default=0,
                )
                requested = ing["kg_required"]

                gap = naive_score - pressure_score
                exceeds_precedent = requested > max_clean_delivery

                if gap > 0.3 or (exceeds_precedent and pressure_score < 0.7):
                    claims.append(Claim(
                        source_agent="SupplyReliabilityAgent",
                        severity="risk",
                        centre_id=req["centre_id"],
                        programme_id=req["programme_id"],
                        summary=f"Low-confidence delivery: {supplier['name']} for {ing['item']}",
                        detail=(
                            f"Requested {requested}kg. Naive reliability {naive_score:.0%}, "
                            f"pressure-adjusted reliability {pressure_score:.0%} "
                            f"(largest volume ever cleanly delivered: {max_clean_delivery}kg). "
                            f"{supplier.get('flag') or ''}"
                        ),
                        recommended_options=[
                            "Pre-emptively source shortfall volume from a backup supplier",
                            "Reduce today's dependent-programme volume now and communicate proactively",
                            "Accept the risk and hold contingency stock on standby",
                        ],
                    ))
        return claims


# ---------------------------------------------------------------------------
# Agent 3 — Programme Priority
# ---------------------------------------------------------------------------

class ProgrammePriorityAgent:
    """Re-ranks by criticality tier, not volume, and detects multi-cycle
    silent deprioritisation trends hidden behind healthy aggregate output."""

    SHORTFALL_THRESHOLD = 0.10  # 10% under plan counts as a shortfall

    def __init__(self, store: DataStore):
        self.store = store

    def tier(self, programme_id: str) -> str:
        return self.store.programmes.get(programme_id, {}).get("criticality_tier", "Unknown")

    def run(self) -> List[Claim]:
        claims = []
        # Group decision log by (centre, programme) to look for a trend across cycles
        by_key: Dict[tuple, List[dict]] = {}
        for entry in self.store.decision_log:
            for pb in entry.get("programme_breakdown", []):
                key = (entry["centre_id"], pb["programme_id"])
                by_key.setdefault(key, []).append({
                    "cycle": entry["cycle"],
                    "planned": pb["planned"],
                    "actual": pb["actual"],
                })

        for (centre_id, programme_id), records in by_key.items():
            tier = self.tier(programme_id)
            if "Tier 1" not in tier:
                continue
            shortfalls = [
                1 - (r["actual"] / r["planned"]) for r in records if r["planned"] > 0
            ]
            consecutive_shortfalls = sum(1 for s in shortfalls if s > self.SHORTFALL_THRESHOLD)
            if consecutive_shortfalls >= 2:
                trend = " -> ".join(f"{s:.0%}" for s in shortfalls)
                claims.append(Claim(
                    source_agent="ProgrammePriorityAgent",
                    severity="critical",
                    centre_id=centre_id,
                    programme_id=programme_id,
                    summary=f"Silent multi-cycle deprioritisation of {tier} programme {programme_id}",
                    detail=(
                        f"Shortfall trend across cycles: {trend}. This programme is "
                        f"{tier} ({self.store.programmes[programme_id]['name']}), but the centre's "
                        f"aggregate output has looked healthy throughout — this is a capacity "
                        f"allocation policy issue, not a one-day fix."
                    ),
                    recommended_options=[
                        "Reserve a fixed minimum share of shared-equipment capacity for this Tier 1 programme",
                        "Review whether current equipment can physically serve both programmes at target volumes",
                    ],
                ))
        return claims


# ---------------------------------------------------------------------------
# Agent 4 — Capacity & Load
# ---------------------------------------------------------------------------

class CapacityLoadAgent:
    """Cross-centre view of physical throughput vs. committed load — the
    coordination no single centre's local logic can provide on its own."""

    def __init__(self, store: DataStore):
        self.store = store

    def committed_load(self, centre_id: str) -> int:
        return sum(
            r["meals_required"] for r in self.store.today_requirements
            if r["centre_id"] == centre_id
        )

    def avg_utilisation(self, centre_id: str) -> float:
        history = self.store.historical_utilisation(centre_id)
        return sum(history) / len(history) if history else 1.0

    def is_fully_clean(self, centre_id: str) -> bool:
        rows = [e for e in self.store.decision_log if e["centre_id"] == centre_id]
        return bool(rows) and all(not r.get("flags") for r in rows)

    def run(self) -> List[Claim]:
        claims = []
        loads = {cid: self.committed_load(cid) for cid in self.store.centres}
        overloaded = []
        slack_centres = []

        for cid, centre in self.store.centres.items():
            capacity = centre["base_daily_capacity_meals"]
            load = loads[cid]
            utilisation = load / capacity if capacity else 0
            if utilisation > 1.0:
                overloaded.append((cid, load, capacity, utilisation))
            elif self.is_fully_clean(cid):
                # Only a centre with a fully clean multi-cycle record is treated as a
                # genuine reallocation donor. A centre showing spare capacity because
                # of its own unresolved risk (e.g. a supply shortfall) is not real slack.
                avg_hist = self.avg_utilisation(cid)
                slack_meals = int(capacity * (1 - avg_hist))
                if slack_meals > 0:
                    slack_centres.append((cid, slack_meals, avg_hist))

        for cid, load, capacity, utilisation in overloaded:
            excess = load - capacity
            # naive greedy match to a slack centre
            donor = max(slack_centres, key=lambda x: x[1], default=None)
            options = [
                f"Hold today's load to {capacity} and negotiate a grace period for the remaining {excess} meals",
            ]
            if donor:
                donor_id, donor_slack, donor_hist = donor
                shiftable = min(excess, donor_slack)
                options.insert(0, (
                    f"Shift {shiftable} meals of steady-state load from {donor_id} "
                    f"(avg utilisation {donor_hist:.1%}, ~{donor_slack} meals/day slack) to cover the gap"
                ))
            claims.append(Claim(
                source_agent="CapacityLoadAgent",
                severity="critical",
                centre_id=cid,
                programme_id=None,
                summary=f"Centre {cid} committed load ({load}) exceeds physical capacity ({capacity})",
                detail=(
                    f"Utilisation would be {utilisation:.0%} of ceiling — {excess} meals over. "
                    f"Ingredients may be fully sufficient; the constraint is physical throughput."
                ),
                recommended_options=options,
            ))
        return claims


# ---------------------------------------------------------------------------
# Agent 5 — Cascade Risk & Dependency
# ---------------------------------------------------------------------------

class CascadeRiskAgent:
    """Builds the ingredient -> recipe -> programme -> centre dependency graph
    for today, and specifically checks for uncoordinated shared-pool claims —
    the failure mode behind the C4 mid-cycle reversal."""

    def __init__(self, store: DataStore, supply_claims: List[Claim]):
        self.store = store
        self.supply_claims = supply_claims

    def run(self) -> List[Claim]:
        claims = []

        # --- Shared stock pool check ---
        pool_claims: Dict[tuple, List[dict]] = {}
        for req in self.store.today_requirements:
            for ing in req.get("key_ingredients", []):
                key = (req["centre_id"], ing["item"])
                pool_claims.setdefault(key, []).append({
                    "programme_id": req["programme_id"],
                    "kg_required": ing["kg_required"],
                })

        for (centre_id, item), claimants in pool_claims.items():
            if len(claimants) < 2:
                continue
            total_claimed = sum(c["kg_required"] for c in claimants)
            inv = self.store.inventory_for(centre_id, item)
            if not inv:
                continue
            on_hand = inv.get("on_hand_kg", 0)
            incoming = sum(i.get("expected_kg", 0) for i in inv.get("incoming", []))
            available = on_hand + incoming

            tier1_involved = any(
                "Tier 1" in self.store.programmes.get(c["programme_id"], {}).get("criticality_tier", "")
                for c in claimants
            )

            if total_claimed <= available:
                # Sufficient supply, but no reservation existed until now — auto-resolve by splitting.
                allocation = ", ".join(
                    f"{c['programme_id']}: {c['kg_required']}kg" for c in claimants
                )
                claims.append(Claim(
                    source_agent="CascadeRiskAgent",
                    severity="info",
                    centre_id=centre_id,
                    programme_id=None,
                    summary=f"Shared stock pool auto-reserved: {item} at {centre_id}",
                    detail=(
                        f"{len(claimants)} programmes ({allocation}) draw from one pool of {available}kg "
                        f"({on_hand}kg on hand + {incoming}kg incoming). Supply is sufficient for all claims — "
                        f"splitting into hard per-programme reservations before production starts, closing the "
                        f"exact gap that caused a prior-cycle mid-shift reversal at this centre."
                    ),
                    auto_resolvable=True,
                    auto_action=f"Reserve: {allocation}",
                ))
            else:
                shortfall = total_claimed - available
                claims.append(Claim(
                    source_agent="CascadeRiskAgent",
                    severity="critical",
                    centre_id=centre_id,
                    programme_id=None,
                    summary=f"Insufficient shared stock: {item} at {centre_id} short by {shortfall}kg",
                    detail=(
                        f"Claims total {total_claimed}kg against {available}kg available. "
                        f"{'A Tier 1 programme is among the claimants.' if tier1_involved else ''}"
                    ),
                    recommended_options=[
                        "Decide which programme's allocation is reduced — requires a policy call, not an auto-split",
                        "Source emergency top-up from a neighbouring centre's buffer stock",
                    ],
                ))

        # --- Propagate supply risk downstream to affected Tier 1 programmes ---
        for sc in self.supply_claims:
            tier = self.store.programmes.get(sc.programme_id, {}).get("criticality_tier", "")
            if "Tier 1" in tier:
                programme_name = self.store.programmes[sc.programme_id]["name"]
                claims.append(Claim(
                    source_agent="CascadeRiskAgent",
                    severity="critical",
                    centre_id=sc.centre_id,
                    programme_id=sc.programme_id,
                    summary=f"Cascade: supply risk threatens Tier 1 programme '{programme_name}'",
                    detail=(
                        f"{sc.summary}. This ingredient is recipe-locked to {programme_name} "
                        f"(Tier 1), a clinically-tracked intervention with low reversibility if missed."
                    ),
                    recommended_options=sc.recommended_options,
                ))
        return claims


# ---------------------------------------------------------------------------
# Agent 6 — Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Classifies claims as auto-resolved vs. human decision points, and
    prints the final day plan. Hard rule: anything touching how much of a
    Tier 1 programme gets produced is always a human decision."""

    def __init__(self, store: DataStore):
        self.store = store

    def touches_tier1(self, claim: Claim) -> bool:
        if not claim.programme_id:
            return False
        return "Tier 1" in self.store.programmes.get(claim.programme_id, {}).get("criticality_tier", "")

    def classify(self, claims: List[Claim]):
        auto, escalate = [], []
        for c in claims:
            if c.auto_resolvable and not self.touches_tier1(c) and c.severity != "critical":
                auto.append(c)
            else:
                escalate.append(c)
        return auto, escalate

    def render(self, claims: List[Claim]):
        auto, escalate = self.classify(claims)

        print("=" * 78)
        print("DAY PLAN — AUTO-RESOLVED ACTIONS (logged, applied, reversible)")
        print("=" * 78)
        if not auto:
            print("  (none)")
        for c in auto:
            print(f"\n[{c.centre_id}] {c.summary}")
            print(f"  Action: {c.auto_action}")
            print(f"  Why: {c.detail}")

        print("\n" + "=" * 78)
        print("HUMAN DECISION POINTS (escalation cards)")
        print("=" * 78)
        if not escalate:
            print("  (none)")
        # sort critical first
        escalate.sort(key=lambda c: {"critical": 0, "risk": 1, "info": 2}[c.severity])
        for i, c in enumerate(escalate, 1):
            print(f"\n--- Card {i} | severity: {c.severity.upper()} | source: {c.source_agent} ---")
            print(f"Centre: {c.centre_id}" + (f" | Programme: {c.programme_id}" if c.programme_id else ""))
            print(f"Issue: {c.summary}")
            print(f"Detail: {c.detail}")
            if c.recommended_options:
                print("Options:")
                for opt in c.recommended_options:
                    print(f"  - {opt}")


# ---------------------------------------------------------------------------
# Run the full pipeline
# ---------------------------------------------------------------------------

def main():
    store = DataStore()

    supply_claims = SupplyReliabilityAgent(store).run()
    priority_claims = ProgrammePriorityAgent(store).run()
    capacity_claims = CapacityLoadAgent(store).run()
    cascade_claims = CascadeRiskAgent(store, supply_claims).run()

    all_claims = supply_claims + priority_claims + capacity_claims + cascade_claims

    Orchestrator(store).render(all_claims)


if __name__ == "__main__":
    main()

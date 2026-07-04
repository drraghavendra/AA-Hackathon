# Dataset Design Notes — Akshaya Patra Production Planning

## Files and join keys

| File | Role | Joins on |
|---|---|---|
| `01_programmes.json` | Programme master data with **criticality tier**, decoupled from volume | `programme_id` |
| `02_centres.json` | Centre master data, capacity, equipment constraints | `centre_id` |
| `03_suppliers_reliability_history.json` | 3-cycle supplier performance, naive vs pressure-adjusted scoring | `supplier_id`, `centre_id` |
| `04_decision_log_cycles1-3.json` | What was planned vs what actually happened, at programme-level granularity, for 3 historical cycles | `centre_id`, `cycle`, `programme_id` |
| `05_planning_report_day4.json` | **The starting condition** — today's raw planning report, exactly as the current (pre-agent) system produces it | `centre_id`, `programme_id` |
| `06_inventory_state_day4.json` | Today's stock + incoming deliveries + equipment/capacity ceilings | `centre_id`, `item` |

## Why the schema is shaped this way

The central design choice is **programme-level granularity everywhere**, not just aggregate centre totals. Nearly every edge case here is invisible at the aggregate level and only appears once you join `decision_log` down to `programme_id` — that's intentional, because that's exactly the gap the real planning report has today.

## Edge cases, explicitly located

**1. Happy path — Centre C1**
`04_decision_log...` Day1–Day3 for C1: steady ~99% utilisation, no programme-level shortfall, no supplier flags. Used as the calibration baseline — this is what "nothing to see here" should actually look like, so the agent doesn't over-flag.

**2. Recency vs. pressure — Supplier SUP-204 (AgroFresh Vegetables), Centre C3**
Two clean cycles at steady-state volume, then a 53%-of-commitment miss on the one cycle that carried a demand surge. `naive_reliability_score` (0.83) says "reliable." `pressure_adjusted_score` (0.35) says "untested-then-failed under the exact load condition forecast for today." Today's report (`05_planning_report_day4.json`) asks this same supplier for 1,900kg — above the volume it has ever cleanly delivered — which is why `06_inventory_state_day4.json` marks that incoming delivery `confidence: LOW` with an explicit basis. **The point:** an agent that reads only the rolling average retrieves the wrong answer; it has to reason about *which* cycle failed and why, not just how many.

**3. Hitting the number, missing the point — Centre C2, Programme PRG-002**
Three consecutive cycles where aggregate output is 97.8%–99.96% of plan (looks excellent) while the Tier 1 anemia programme is under-delivered by 14% → 18% → 21%, a widening trend, masked by over-producing the larger Tier 3 programme on the same shared fortification line. **The point:** volume-sorted retrieval will rank C2 as a top performer. Only a programme-criticality-aware check surfaces that the centre has been trading away a clinically-tracked intervention to hit a bigger, easier number — three times running, not once.

**4. Mid-cycle reversal — Centre C4, Day2**
The `timeline_of_reversal` block in `04_decision_log...` is the useful artifact. Both ingredient stock (2,000kg fortified rice) *and* supplier delivery (`SUP-450`, clean record) checked out fine at planning time — a stock-level or supplier-level check would have approved the plan. The failure was an **allocation gap**: two Tier 1 recipes claimed the same undifferentiated stock pool with no reservation logic, and it only surfaced when the second line physically ran out mid-cook. Today's `06_inventory_state_day4.json` reproduces the identical unresolved condition (PRG-002 + PRG-005 combined draw against one pool) so the agent can be evaluated on whether it catches it *before* production this time.

**5. Demand surge overwhelming steady-state capacity — Centre C5**
Introduced only in `05_planning_report_day4.json` (a new government order, no history to pattern-match against). Ingredients are sufficient (`06_inventory_state_day4.json` shows adequate rice/milk stock); the binding constraint is the 42,000-meal physical throughput ceiling against a 46,000-meal combined ask. **The point:** this is a pure capacity-graph problem, unsolvable by any ingredient-level retrieval, and it's the scenario meant to trigger cross-centre load-shifting (C1 has ~500 meals/day of slack historically, C3/C4 do not, given their own live risks).

## What "retrieval alone isn't enough" means in practice, per file

- Supplier file: retrieval gives you an average; the task needs a conditional average (performance *given* load condition).
- Decision log (C2): retrieval gives you a total; the task needs a decomposition by criticality tier before the total means anything.
- Decision log (C4): retrieval gives you "sufficient stock" and "reliable supplier"; the task needs an allocation/reservation check that neither of those fields expresses.
- Planning report + inventory (C5): retrieval gives you ingredient sufficiency; the task needs a separate physical-throughput constraint that isn't an ingredient at all.

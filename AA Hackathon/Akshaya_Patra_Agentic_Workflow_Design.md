# Agentic Production Planning Workflow — Akshaya Patra

## 1. What this system is for

The daily planning report tells a Production Executive *what's needed*. It does not tell them *what's possible*, *what's urgent if something has to give*, or *what will break somewhere else if they make a local fix*. This workflow sits between the planning report and the production floor. It doesn't replace the Production Executive's judgment — it removes the blind spots that force them into reactive firefighting, and it hands them a small number of well-framed decisions instead of a wall of numbers.

The design principle running through every stage below: **retrieval tells you what a field says; reasoning tells you what a field means given everything around it.** Every agent in this pipeline exists because a single data point, read in isolation, is either misleading or incomplete.

---

## 2. Architecture — six specialised agents + one orchestrator

Rather than one monolithic "planner" agent, the workflow is split so that each agent owns one kind of judgment and produces a structured claim, not a final number. The orchestrator composes those claims and is the only place where trade-offs across agents get resolved — that separation is what makes the reasoning auditable instead of a black box.

```
Daily Planning Report (raw)
        │
        ▼
[1] Ingestion & Normalisation Agent
        │  → structured demand: centre × programme × ingredient
        ▼
[2] Supply Reliability Agent ──────┐
[3] Programme Priority Agent ──────┼──►  [5] Cascade Risk & Dependency Agent
[4] Capacity & Load Agent ─────────┘             │
                                                  ▼
                                   [6] Orchestrator / Reconciliation Agent
                                                  │
                              ┌───────────────────┼───────────────────┐
                              ▼                                       ▼
                   Auto-resolvable actions                 Human decision points
                (logged, applied, reversible)          (escalation cards to PE/RM)
                                                  │
                                                  ▼
                                     [7] Feedback & Learning Loop
                              (decision log → updates scores for next cycle)
```

### [1] Ingestion & Normalisation Agent
Parses the raw planning report into a common schema (centre, programme, ingredient, quantity). Flags anything with no prior-cycle precedent (a brand-new programme, a first-time quantity spike) as `NOVEL — no historical pattern to reason from`, so downstream agents don't silently assume steady-state behaviour applies.

### [2] Supply Reliability Agent
For every required ingredient, does not just check "is the committed quantity on the books." It computes **two** scores per supplier:
- *Naive reliability*: rolling on-time-delivery rate over the last N cycles.
- *Pressure-adjusted reliability*: the same rate, but conditioned on load — how did this supplier perform on cycles that matched today's demand profile (steady-state vs. surge)?

If today's required volume from a supplier exceeds anything they've cleanly delivered before, or matches a load condition under which they previously failed, this agent emits a `SUPPLY_RISK` claim with a confidence level — even if the naive average looks fine. This is the direct answer to "a supplier reliable for two cycles who missed a critical commitment during a demand surge."

### [3] Programme Priority Agent
Holds the criticality tier for every programme (Tier 1 clinically-tracked interventions, Tier 2 contractually high-visibility, Tier 3 standard) independent of volume. Its job is to re-rank today's requirements by *what happens if this is missed*, not by *how big it is*. It also inspects the **decision log**, not just today's report: if a Tier 1 programme has been under-delivered for multiple consecutive cycles while the centre's aggregate number looked healthy, it raises a `SILENT_DEPRIORITISATION` claim — the centre-C2 pattern, where hitting the target masked which target was actually being hit.

### [4] Capacity & Load Agent
Maintains a live, cross-centre view of physical throughput (not just ingredient sufficiency): production-line capacity, shared-equipment ceilings (e.g., a single fortification line serving two programmes), and today's committed load per centre. Detects two distinct failure modes:
- A single centre's ask exceeds its physical ceiling (the C5 surge case) — ingredients can be perfectly sufficient and the centre still can't produce the volume.
- Two centres are making load decisions with no visibility into each other, risking a coordinated shortfall or duplicated slack.

Because this agent has cross-centre visibility by design, it's the mechanism that answers "production managers at different centres making conflicting capacity calls in isolation" — the fix here isn't a smarter local agent, it's a shared state that no single centre's agent could produce alone.

### [5] Cascade Risk & Dependency Agent
This is the core "catch it before it materialises" component. It builds a live dependency graph for the day: `ingredient → recipe → programme → centre`, and specifically checks for **shared inputs across recipes** — the exact gap that caused the C4 mid-cycle reversal (two Tier 1 recipes silently drawing from one undifferentiated fortified-rice pool). For every ingredient flagged at-risk by Agent [2], it walks the graph forward to enumerate every programme and centre downstream, and tags each with the programme's criticality tier from Agent [3]. This is what turns "a supplier might be short 1,000kg of spinach" into "this specifically threatens a Tier 1 anemia intervention at Centre C3, affecting ~1,100 children, and there is no listed backup supplier" — the sentence a Production Executive actually needs, not the sentence a spreadsheet gives them.

Critically, this agent also runs a **pre-commitment allocation check**: before the day's plan is finalised, does more than one downstream consumer claim the same finite stock pool without an explicit split? If yes, it forces the reservation decision to happen now, on paper, rather than on the production line at 09:40am.

### [6] Orchestrator / Reconciliation Agent
Takes the claims from [2]–[5] and produces the actual day plan: sequencing (what gets produced first when something must give), reallocation proposals (shift X meals of load from C1, which has slack, to cover C5's surge), and a decision on what's auto-resolvable vs. what needs a human. It never silently overrides a Tier 1 programme's allocation — that always routes to a human decision point, by design (see §4).

### [7] Feedback & Learning Loop
Every accepted plan, every override, and every reversal gets written back into the decision log in the same programme-level, timestamped format shown in `04_decision_log_cycles1-3.json`. This is not an afterthought: the C4 reversal is only valuable as training signal because it's logged with *why* the check failed (allocation, not supply), so the same dependency check gets tightened for next cycle rather than the same failure repeating with a different pair of programmes.

---

## 3. Reasoning pipeline, step by step (what actually gets computed)

1. **Normalise today's report** against the historical schema; flag novel items (no pattern to match).
2. **Score every ingredient dependency** for reliability — naive and pressure-adjusted — and propagate a confidence level onto every downstream requirement that depends on it.
3. **Re-rank all requirements** by criticality tier, not quantity. Cross-check against the rolling decision log for any Tier 1 programme showing a multi-cycle shortfall trend, even if today's number alone looks fine.
4. **Build the day's capacity graph**: per-centre ceiling vs. committed load, and per-shared-equipment ceiling (e.g., one fortification line, two programmes).
5. **Build the day's ingredient dependency graph**: which recipes draw from which stock pools, and whether any pool has more than one uncoordinated claim on it.
6. **Simulate "what if"**: for every flagged risk (low-confidence delivery, over-capacity centre, contested stock pool), generate 1–2 concrete resolution options with their trade-offs (e.g., "shift 4,000 meals of PRG-001 from C5 to C1's slack capacity" vs. "hold C5's surge order to 42,000 and request 1-day grace on the remaining 4,000 from the government liaison").
7. **Classify every flagged item** as auto-resolvable or human-decision (§4).
8. **Emit the day plan**: sequencing, quantities, reallocation instructions, and an escalation card per human-decision item.
9. **Log outcomes at end of cycle** back into the decision log at programme-level granularity, updating supplier scores and capacity assumptions for tomorrow.

---

## 4. What's auto-resolved vs. what goes to a human

A hard rule, not a heuristic: **anything that would change how much of a Tier 1 (clinically-tracked) programme gets produced is always a human decision.** The agent's job there is to make the trade-off fully visible, not to make the call.

**Auto-resolvable (agent acts, logs the action, flags it as reviewable):**
- Reordering production sequence within a centre when no criticality trade-off is involved.
- Shifting Tier 3 load between two centres that both have confirmed slack and no risk flags.
- Splitting a shared ingredient pool into per-programme reservations when total supply is sufficient for all claims (removes the C4 failure mode without needing a human in the loop every time).

**Human decision point (escalation card, not a buried log line):**
- *Supply risk on a Tier 1 dependency* (the C3 case): "SUP-204's confirmed delivery for today is 1,900kg. Under comparable surge load last cycle, they delivered 47% of commitment. If that repeats, Centre C3's Anganwadi iron-fortification programme (≈1,100 children) will be short. Options: (a) pre-emptively source 900kg from backup supplier list, (b) reduce today's PRG-002 volume now and communicate proactively, (c) accept the risk and hold contingency stock on standby. Recommend (a) given lead time."
- *Multi-cycle silent deprioritisation* (the C2 case): "Centre C2 has under-delivered the Tier 1 anemia programme by 14%→18%→21% over the last 3 cycles while reporting 99%+ aggregate output. Root cause: shared fortification-line capacity defaults to the larger programme. This needs a capacity allocation policy decision, not a one-day fix."
- *Capacity ceiling breach from a demand surge* (the C5 case): "New government order pushes C5 to 109% of physical capacity. Recommend shifting 4,000 meals of steady-state PRG-001 load to Centre C1 (has averaged 0.6% slack over 3 cycles). Requires sign-off since it changes a committed school's delivery source."
- *Any allocation conflict discovered on a Tier 1 shared input* where supply is actually insufficient for both claims (not just a coordination fix) — someone has to decide which clinical programme gets priority, and that is a human call by policy, every time.

---

## 5. Worked walkthrough on the Day4 dataset

Using `05_planning_report_day4.json` and `06_inventory_state_day4.json` as the starting condition:

- **C1**: no flags. Slack confirmed by 3-cycle clean history → nominated as the safety valve for C5's surge.
- **C2**: today's numbers alone look fine, but Agent [3] pulls the 3-cycle trend and raises `SILENT_DEPRIORITISATION` on PRG-002 before production starts, rather than letting a 4th quiet shortfall happen — escalated as a policy-level card, not a one-day tweak.
- **C3**: Agent [2] flags SUP-204's incoming 1,900kg delivery as `LOW confidence` (pressure-adjusted score 0.35, volume exceeds anything ever cleanly delivered). Agent [5] walks this to PRG-002 (Tier 1) and generates the escalation card above before the delivery window even opens — catching it a full cycle earlier than last time, when it was only discovered as a shortfall mid-production.
- **C4**: Agent [5]'s pre-commitment allocation check finds PRG-002 (1,400kg) + PRG-005 (900kg) = 2,300kg claimed against a 2,100kg-on-hand + 2,100kg-incoming pool. Supply is sufficient, so this is auto-resolved: the pool is split into two hard reservations before the shift starts, closing exactly the gap that caused the Day2 reversal — no human needed this time because supply covers both claims.
- **C5**: Agent [4] flags the 46,000-meal ask against a 42,000 ceiling. Orchestrator proposes shifting 4,000 meals of PRG-001 to C1, generates the escalation card, and holds the plan pending Regional Manager sign-off since it reroutes a committed school's supply source.

The Production Executive at each centre wakes up to a short list of decisions with reasoning attached, not a longer version of the same report.

---

## 6. Why this design, briefly

- **Programme-level granularity everywhere** — because the C2 case shows aggregate output actively conceals the failure mode the whole system exists to prevent.
- **Two-track reliability scoring** — because a rolling average and a stress-tested average answer different questions, and the report only ever showed the first one.
- **A dependency graph, not a lookup table** — because the C4 failure wasn't a missing fact (stock existed, supplier delivered), it was a missing *relationship* between two claims on the same fact.
- **Cross-centre shared state** — because isolated centre-level optimisation is structurally incapable of catching the C5-style surge or coordinating slack, no matter how good any single centre's local logic is.
- **A hard rule for what stays human** — because the goal isn't full automation, it's giving a Production Executive the right five decisions instead of the wrong five hundred numbers.

Full dataset with schema notes and edge-case annotations: see `dataset/00_dataset_readme.md`.

# Mapping to Automation Anywhere Pathfinder / AI Agent Studio

**A note on scope, upfront:** I don't have access to Automation Anywhere's current product documentation, and their platform naming/capabilities evolve — treat the component names below as a *blueprint of the mapping logic*, and verify exact current feature names/steps against AA's own docs (`docs.automationanywhere.com`) or the hackathon's provided platform guide before building. What won't go stale is the underlying architecture: which reasoning responsibility goes where, and why it needs to stay separated the way it does in the Python prototype.

## Why port it at all, rather than just submit the Python

A hackathon built specifically around Automation Anywhere is almost certainly judged partly on *platform fluency* — showing you can use their actual agent/orchestration primitives, not just that you can write Python. The Python prototype proves the reasoning logic is correct and demonstrable end-to-end. The AA build proves you can operationalize it on their stack, which is very likely the real ask of a "Pathfinder" hackathon.

## Component mapping

| Python prototype component | AA Pathfinder equivalent | Notes |
|---|---|---|
| `load_dataset()` | Document/data ingestion via **Bot** reading from a database, SharePoint/Excel connector, or IQ Bot-style document extraction on the daily planning report | If the "planning report" is a real Excel/PDF in production, this is where AA's document automation genuinely earns its keep over a plain script |
| `SupplyReliabilityAgent` | An **AI Agent** (in AI Agent Studio) with a dedicated **Skill**: "Assess Supplier Reliability" — backed by a generative action/prompt that takes structured history as input and returns a structured risk score | Keep the naive-vs-pressure-adjusted *calculation* as deterministic bot logic (a Python/JS action or a formula step) rather than asking the LLM to do arithmetic from scratch — use the LLM for the judgment call ("does this pattern indicate risk"), not the number crunching |
| `ProgrammePriorityAgent` | A Skill/Agent: "Programme Criticality Check" reading a reference data table (tier, reversibility) plus a bot-side query against historical logs | The multi-cycle trend detection is exactly the kind of thing to keep as a rules/query step (reliable, auditable) with the LLM only writing the human-readable explanation on top |
| `CapacityLoadAgent` | A Skill: "Cross-Centre Capacity Check" — this is the one that most benefits from AA's **Process orchestration** layer, since it needs a live, shared data source across centres rather than per-centre local state | Model this as a shared data table/queue in AA rather than letting each centre's bot run independently — mirrors the design doc's point that isolated local optimization is structurally incapable of catching this |
| `CascadeRiskAgent` | An **Agent** composing outputs from the two agents above plus an ingredient-dependency lookup — in AI Agent Studio this is a good candidate for an agent that *calls other agents/skills* as sub-tasks rather than doing everything itself | The allocation-conflict check (shared stock pool, two claimants) is deterministic logic — implement as a bot action, not a prompt, so it's not subject to LLM inconsistency on something that has one correct answer |
| `Orchestrator` | The top-level **AI Agent** / **Process** that sequences the above, applies the "Tier 1 change = always human" rule, and generates escalation cards | This is also where you'd wire in AA's **human-in-the-loop / approval step** functionality — the escalation cards map directly onto an approval task assigned to the Production Executive or Regional Manager |
| `print_summary()` / JSON output | A generated report/dashboard step, or a message posted into a Teams/Slack/email channel via AA connectors, plus the approval task UI for escalations | The escalation card format in the Python output (priority, reason, explanation, recommended action) is designed to map directly onto a task/approval card UI |
| Feedback loop (decision log) | A step writing outcomes back into a database/table that feeds next cycle's Skill inputs | This is what makes the system actually learn cycle-over-cycle rather than reasoning fresh each time — don't skip this in the platform build, it's one of the more differentiated things to demo |

## What to actually build for a demo, given limited time

Priority order if you can't build the whole thing on-platform:
1. **One end-to-end Process** that ingests the Day4 planning report and inventory data (even from a spreadsheet), runs the capacity-ceiling check and the allocation-conflict check as bot logic, and raises a human approval task for the C5 capacity breach. This alone demonstrates ingestion → reasoning → human-in-the-loop on the platform.
2. **One AI Agent Studio Skill** that does the supplier reliability judgment (naive vs. pressure-adjusted) — this is the single most "agentic reasoning, not just RPA" piece, and worth having even if the rest stays as a described architecture.
3. Everything else can be presented as architecture + the working Python simulation, with a clear statement of "this maps to AA components X/Y/Z as follows" (i.e., this document) — judges generally respond well to an honest, well-reasoned scope cut over a rushed, broken full build.

## How to present all three pieces together in the submission

- **Design doc** (`Akshaya_Patra_Agentic_Workflow_Design.md`) — the architecture and reasoning, platform-agnostic.
- **Dataset** (`dataset/`) — proof that the design was built against real edge cases, not just a happy-path demo.
- **Python prototype** (`production_planning_agent.py` + `day4_plan_output.json`) — proof the reasoning logic actually produces correct, auditable output against that dataset. Use this in your demo video/walkthrough even if the platform build is partial — it's your fallback that always works.
- **AA platform build** (however much you complete) — proof of platform fluency, framed explicitly against this mapping table so judges can see the translation even where the build is partial.

This four-piece structure also directly answers a question judges are likely to ask: "why is this agentic and not just a report?" — because you can point to the human-in-the-loop escalation design and the cascade/dependency reasoning as the parts no simple automation or dashboard would produce.

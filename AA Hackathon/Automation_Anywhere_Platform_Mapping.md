# Mapping to Automation Anywhere — Platform Build Notes

> **Caveat up front:** I don't have verified, current documentation for Automation
> Anywhere's AI Agent Studio / Process Reasoning / connector catalog in front of me,
> and product surfaces change fast. Treat the mappings below as an architectural
> translation of the *logic* already proven in `agentic_workflow.py`, not as exact
> menu paths or feature names. Before you build, skim AA's current docs (or the
> hackathon's provided starter kit) to confirm the equivalent building blocks —
> this doc tells you *what* to look for, not the literal click-path.

## Why build both

Judges typically want to see two different things, and neither substitutes for the other:
1. **The reasoning is correct** — easiest to prove with a fast, transparent, dependency-free script they can run and inspect line by line. That's what `agentic_workflow.py` is for.
2. **It's realistic as an enterprise automation** — needs to show up in the platform the hackathon is actually about, using real connectors, human-in-the-loop task queues, and audit trails, not a terminal printout.

The mapping below carries the exact same six-agent design across.

## Component mapping

| Prototype component | Automation Anywhere equivalent (verify exact names in current docs) |
|---|---|
| `DataStore` (JSON loaders) | Data connectors / integrations pulling from the real systems: ERP or inventory management system, supplier portal or SFTP feed, centre production telemetry. Each becomes a bot input action or a connector call feeding a shared data table. |
| `Claim` dataclass | A structured record type (e.g., a table/queue schema) with fields: source agent, severity, centre, programme, summary, detail, options, auto_resolvable. This is what flows between agents/bots instead of a Python object. |
| `SupplyReliabilityAgent` | A dedicated agent (or bot with an AI/Generative skill step) that reads supplier delivery history, computes the two reliability scores, and writes risk records to the shared queue. This is a good candidate for a reusable "skill" if the platform supports composable agent skills. |
| `ProgrammePriorityAgent` | An agent that reads the historical production log and programme master data, and applies the trend-detection rule (consecutive shortfalls against a threshold) — a plan-analysis / business-rule step, not a generative one; keep this deterministic rather than delegating the threshold logic to an LLM prompt. |
| `CapacityLoadAgent` | An agent with visibility across all centres' committed load and capacity — this is the one component that structurally requires a **shared/central store**, not a per-centre bot instance, otherwise you reproduce the "centres in isolation" problem the whole build exists to solve. |
| `CascadeRiskAgent` | An agent that joins today's requirements against inventory and the dependency graph. If the platform has a native graph/relationship modelling feature, use it here; otherwise a relational join across two tables reproduces the same logic. |
| `Orchestrator` | The top-level agent/process that calls the four specialist agents, applies the hard auto-resolve-vs-escalate rule, and routes escalation cards into a **human task/approval queue** (this is usually a first-class primitive on agent-orchestration platforms — look for "human-in-the-loop step" or "approval task"). |
| Escalation cards (console output) | A real task assigned to a Production Executive / Regional Manager persona, with the same summary/detail/options fields, inside the platform's task inbox — this is the piece that actually demonstrates "the right human intervention point" to judges, more convincingly than a printed card. |
| Feedback & Learning Loop | Whatever decision gets made on an escalation (approved / overridden / modified) gets written back into the same historical log table, so next cycle's `ProgrammePriorityAgent`/`SupplyReliabilityAgent` reasoning picks it up automatically — implement this as a callback/write-back step after the human task resolves, not a separate manual process. |

## What to keep deterministic vs. what to delegate to an LLM step

Not every agent needs a generative model behind it, and judges tend to reward knowing the difference:

- **Rule-based / deterministic** (keep as plain logic, same as the Python prototype): reliability scoring math, shortfall-trend detection, capacity arithmetic, shared-pool allocation checks. These need to be auditable and repeatable — an LLM call here adds risk without adding value.
- **Language-model-appropriate**: turning a claim's structured fields into the natural-language escalation card text a human reads; summarising *why* a recommendation was made in plain language; drafting the proactive communication to a school/government office if a program's volume is being reduced. This is where an agent-with-a-prompt genuinely earns its place over a bot with hardcoded strings.

## Suggested demo narrative for judging

1. Show the Python run first — fast, and proves the reasoning is sound and testable end-to-end in seconds (this doubles as your automated test harness for the platform build).
2. Then show the same dataset flowing through the AA build, ending in a real task-queue entry — closes the loop from "logic is correct" to "this is a deployable enterprise automation."
3. Explicitly point out the one thing a pure retrieval/dashboard tool couldn't have caught (pick the C4 shared-pool case or the C2 silent-deprioritisation case — both are visually clear) and show your build catching it.

## Honest gaps to flag to judges rather than hide

- The dataset is synthetic and hand-authored to exercise specific edge cases; it is a reasoning-evaluation set, not a production data extract — say so plainly.
- The Python prototype and the platform build should be reasoning-equivalent, but if you only have time to fully wire one path through the AA platform, wire the C4 (shared stock pool) and C5 (capacity surge) cases — they're the clearest to demo live and don't require faking a multi-day historical trend on stage.

# Integration Guide — Wiring the Pipeline into Automation Anywhere

**Caveat, as before:** exact menu names/steps for AA's current platform aren't something I can verify without their live docs — check `docs.automationanywhere.com` or the hackathon's starter kit. What's verified below is that the *code actually runs correctly end-to-end* against your real dataset file: both integration paths were tested against `Akshaya_Patra_Dataset.xlsx` and produced correct results before this was handed to you.

## What changed from the earlier prototype

`aa_agentic_pipeline.py` reads **directly from `Akshaya_Patra_Dataset.xlsx`** — the actual workbook you'd hand to a bot — instead of the intermediate JSON files. Tested and confirmed:

```
Escalation cards: 3
  [HIGH] SILENT_DEPRIORITISATION — C2 PRG-002
  [HIGH] CAPACITY_CEILING_BREACH — C5
  [HIGH] SUPPLY_RISK — C3 PRG-002
Auto-resolved actions: 2  (C4 fortified-rice reservation, C5 rice reservation)
```

One real bug was caught and fixed while wiring this up: the workbook's `Planning_Report_Day4` sheet is flattened to one row per ingredient, so a programme with two tracked ingredients (like C5's surge programme, rice + milk) would have its meal count summed twice if you naively `groupby().sum()`. Fixed by deduplicating to one row per (centre, programme) before aggregating — worth knowing about if you extend the sheet further, since it's the exact kind of aggregation error the whole system is designed to catch elsewhere.

## Two integration paths, pick based on your bot runner's setup

### Path A — Python Script action (if your AA runner has Python + pandas/openpyxl available)
1. Deploy `aa_agentic_pipeline.py` alongside the dataset file on the machine running the bot.
2. Bot step: **Run Python Script** (or equivalent "Python package" command), calling:
   ```
   python3 aa_agentic_pipeline.py --dataset Akshaya_Patra_Dataset.xlsx --outdir ./output
   ```
3. Bot step: **Excel/CSV Advanced — Open** `output/escalation_cards.csv`, then **loop through rows** — for each row, create a work-queue item / approval task assigned to the relevant Production Executive or Regional Manager, populating the task with `priority`, `reason`, `explanation`, `recommended_action`.
4. Separately loop `output/auto_resolved_actions.csv` into an audit-log write or notification step — no human task needed for these.

### Path B — REST Web Service action (more portable; works even if the bot runner has no Python)
1. Deploy `aa_rest_api.py` on any server with Python + Flask + pandas + openpyxl installed (tested locally: `POST /run-plan` with the dataset as a multipart file returns HTTP 200 and the full JSON in under a second).
2. Bot step: **REST Web Service**, method POST, multipart/form-data, field name `dataset`, pointed at `https://<your-host>/run-plan`.
3. Parse the JSON response's `escalation_cards` array in the bot (AA's JSON-handling actions), loop through it the same way as Path A.

Path B is the one to prefer if you're not sure the AA runner environment has Python packages installed — it moves the dependency onto a server you control instead.

## Files in this package

| File | Purpose |
|---|---|
| `aa_agentic_pipeline.py` | Core logic — reads the xlsx dataset, runs all reasoning checks, writes CSV + JSON output |
| `aa_rest_api.py` | Flask wrapper exposing the same logic as a REST endpoint |
| `output/escalation_cards.csv` | Sample output — one row per human decision point, ready to loop into a task queue |
| `output/auto_resolved_actions.csv` | Sample output — actions taken automatically, for audit logging |
| `output/day_plan_output.json` | Same result as a single JSON payload |

## What to say to judges about this specific piece

This is the part that answers "does the reasoning actually work against the real artifact, not just a made-up example" — it was run against the exact `.xlsx` file you're submitting as the dataset, not a hand-typed test case, and a genuine bug (the ingredient-row double-count) was caught and fixed in the process of proving that out. That's a stronger claim than "the logic should work."

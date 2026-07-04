Akshaya Patra feeds millions of children every day. Every morning, a Production Executive opens a planning report — ingredients required, quantities per centre, programmes to fulfill. The report tells them what's needed for the day. It doesn't tell them what's possible, what's critical, or what to do when the two don't match.

THE CHALLENGE
Without proper support, production managers risk failures such as:
• Deprioritising a nutritionally critical programme by accident because the planning report surfaces volume, not urgency
• A single ingredient shortage cascading into missed meals across multiple centres because the dependency wasn't visible at decision time
• Production managers at different centres making conflicting capacity calls in isolation, with no shared view of load distribution
• Demand surges — a sudden programme expansion or government order — overwhelming a schedule built for steady-state operations
All of this forces reactive intervention on decisions that should have been caught before production started.

YOUR MISSION
Build an agentic workflow that reads the daily planning report as a starting condition, then reasons across inventory state, supplier reliability, programme priority, and distributed production capacity to determine what should actually be produced today, in what order, and where production load should shift across centres. The agent should catch cascade risks before they materialise and surface the right human intervention points.

DESIGNING THE DATASET
Show the agent reasoning across interdependent variables, hitting edge cases where a single data point is misleading, and surfacing where retrieval alone isn't enough.
• Happy path: a centre with a clean production record across 2–3 planning cycles — consistent output, no supplier flags, no capacity overruns.
• Edge cases: a supplier reliable for two cycles who missed a critical commitment during a demand surge (recency vs pressure); a centre that hit output targets only by deprioritising a nutritionally critical programme (what's under the numbers?); a production decision reversed mid-cycle because an ingredient dependency wasn't caught — the reversal is the useful data point.

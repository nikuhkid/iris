# IRIS — Inference, Response & Input System

*A controlled AI runtime where models suggest, the system decides, and execution is gated.*

**Status: 🔧 Architecture complete — implementation in progress**

---

## What This Is

IRIS is not an assistant. It's a controlled execution runtime with a personality interface.

The distinction matters: most AI systems give a model authority over what happens. IRIS inverts that. The model is an advisor. The system is the authority. Nothing executes without passing through deterministic gates — plan analysis, decision engine, dry run, user confirmation.

The result is a system that feels responsive and intelligent at the surface while remaining provably controlled underneath.

---

## Core Principle

> **Models suggest. System decides. Execution is gated.**

---

## Architecture

The full system spec lives in [`architecture_4.0.json`](./architecture_4.0.json).

### Execution Flow

```
entry_point (cli | discord | interface)
  → input_guard
  → translation_layer (slot 1 — planning model)
  → plan_analysis_initial  (facts only — deterministic)
  → selective_redundancy   (slot 2 independent call if write/delete/state_change)
  → comparator             (conflict → hard stop, no auto-resolution)
  → plan_analysis_final    (interpretation only — facts immutable)
  → observer_log
  → decision_engine        (final authority — no model touches this)
  → dry_run
  → user_action            (approve | modify | reject | kill)
  → execution
```

### Key Design Decisions

**Two-pass plan analysis** — Pass 1 extracts deterministic facts only. Pass 2 interprets. Facts from Pass 1 are immutable. Debuggability guarantee: if something misfires, you know exactly which pass broke.

**Independent redundancy** — For high-risk operations, a second model call runs against the original input (never Slot 1's output). Conflict between the two → hard stop, escalate to user. No auto-resolution. Silent conflict resolution is how you get confident wrong answers.

**Loss-aversion rules** — Asymmetric confirmation thresholds. The cost of asking when unnecessary is always less than the cost of acting when wrong. Unknown action type → always reject. Irreversible action → always confirm, never scored away.

**Separated model roles** — Planning model outputs strict JSON only, no personality. Response model handles user-facing natural language only. DPO fine-tuning applies to the response model exclusively — tone and register, never structure or execution behavior.

**Epistemic contract** — Training data is treated as prior, not fact. Facts come from context, tools, or verified sources only. A model that confabulates confidently is worse than one that says "I don't know."

---

## Model Stack

| Slot | Model | Role |
|------|-------|------|
| 1 | Qwen2.5 7B local (Ollama, Q4_K_M) | Primary — default path, temp 0.2 |
| 2 | Qwen2.5 7B local (Ollama, Q4_K_M) | Independent validator, temp 0.3 |
| 3 | Claude API | Last resort — repeated schema failure only |

Slot 3 never resolves Slot 1 vs Slot 2 conflicts. Conflicts are a human problem.

7B is the current deployed model. Migration to 14B is Phase 6 scope (blocked on VRAM — requires Wolfie or quantization strategy).

---

## What IRIS Is Not

- Not autonomous — every destructive or state-changing action requires confirmation
- Not a chatbot — the personality layer is a presentation concern, not an authority
- Not finished — implementation is ongoing

---

## Status

- [x] Architecture designed and versioned (`4.0`)
- [x] Two-pass plan analysis specified
- [x] Slot chain with failure semantics defined
- [x] Loss-aversion rules formalized
- [x] Model layer separated (planning vs response)
- [x] DPO scope bounded
- [x] Phase 1 — skeleton (validator, plan analysis, decision engine, pipeline)
- [x] Phase 2 — reliability (retry logic, SQLite logging, input variation testing)
- [x] Phase 3 — redundancy (slot 2, comparator, conflict escalation)
- [x] Phase 4 — control layer (dry run, approval loop, decision tracking)
- [x] Phase 5 — observability (SHA-256 hash chain, degraded mode, tamper detection) — 94/94 smoke tests passing
- [ ] Phase 6 — agent runtime (design in progress — 3 sessions remaining)
- [ ] Local model migration (7B → 14B)
- [ ] Local model training pipeline
- [ ] Discord transport
- [ ] Phone client

---

## Specification Documents

| Document | Covers |
|---|---|
| [`docs/IRIS_Runtime_Contract.docx`](./docs/IRIS_Runtime_Contract.docx) | Agent event model, transport, backpressure, heartbeat, cancellation, hierarchy (v4.2) |
| [`docs/IRIS_Agent_Structure.docx`](./docs/IRIS_Agent_Structure.docx) | Registry, policy shape, coordinator, spawn mappings (v4.3) |
| [`docs/IRIS_Policy_Profiles.docx`](./docs/IRIS_Policy_Profiles.docx) | Concrete policy profile contents per worker |
| [`docs/memory_architecture.md`](./docs/memory_architecture.md) | Three-axis memory model — deferred |

---

## Related

- [mnem](https://github.com/nikuhkid/mnem) — the persistent memory and infrastructure layer IRIS runs on

---

*IRIS — Inference, Response & Input System*

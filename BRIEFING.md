# IRIS — Session Briefing Document
*Load this at the start of any IRIS build session. This is the source of truth.*

---

## What IRIS Is

**IRIS — Inference, Response & Input System**

A controlled AI runtime. Not an assistant. The model is an advisor. The system is the authority. Nothing executes without passing through deterministic gates.

Named in the Tony Stark tradition — every word in the acronym is something a 4th grader can say. The complexity is invisible.

**Core principle:** Models suggest. System decides. Execution is gated.

**Repo:** https://github.com/nikuhkid/iris
**Local path:** `/home/nikuhkid/iris/`
**Architecture spec:** `/home/nikuhkid/iris/architecture_4.0.json`

---

## What Mnem Is (Related Infrastructure)

Mnem is the persistent memory and execution infrastructure IRIS runs on. Separate repo, separate concern.

**Repo:** https://github.com/nikuhkid/mnem
**Local path:** `/home/nikuhkid/.claude/mnem/`

---

## Architecture 4.0 — Key Decisions

### Execution Flow
```
entry_point (cli | discord | interface)
  → input_guard
  → translation_layer → slot_1 → plan
  → plan_analysis_initial (facts only — deterministic)
  → selective_redundancy gate (slot_2 with original_input if write/delete/state_change)
  → comparator (conflict → hard stop, escalate to user)
  → plan_analysis_final (interpretation only — facts immutable)
  → observer_log
  → decision_engine (final authority — no model touches this)
  → dry_run
  → user_action (approve | modify | reject | kill)
  → execution
```

### Model Layer — Two Roles, Never Mixed
| Layer | Role | DPO? |
|-------|------|------|
| planning_model | Strict JSON only. No personality. No flair. | No |
| response_model | Natural language only. Personality layer. | Yes — tone only |

### Slot Chain
| Slot | Model | Role | Temp |
|------|-------|------|------|
| 1 | Qwen2.5 7B local (Ollama, Q4_K_M) | Primary | 0.2 |
| 2 | Qwen2.5 7B local (Ollama, Q4_K_M) | Independent validator | 0.3 |
| 3 | Claude API | Last resort — schema failure only | — |

**Slot 2 receives original_input — never slot_1 output. Independence is the point.**
**Slot 3 never resolves slot_1 vs slot_2 conflicts. Conflicts are a human problem.**

### Plan Analysis — Two Pass, Strictly Separated
- **Pass 1 (plan_analysis_initial):** Facts only. Action types, args, step count. Raw term extraction for staging. No interpretation.
- **Pass 2 (plan_analysis_final):** Interpretation only. Consumes pass 1 output. Facts are immutable — cannot be modified.
- **Debuggability guarantee:** If something misfires, you know exactly which pass broke.

### Implicit Destructive Detection — Three-Step Gate
All three must pass before flagging:
1. Lexical match against term list: `clean, clear, remove, prune, reset, purge, wipe, flush`
2. Scope gate: write OR delete must be true (eliminates "clear explanation" false positives)
3. Argument context: term must appear in file paths, args, or action descriptions — not commentary

### Decision Engine — Hard Rules, No Model
- Irreversible action → always confirm, never scored away
- Unknown action type → always reject
- Implicit destructive → require confirmation
- Cost of asking when unnecessary < cost of acting when wrong

### DPO Scope — Bounded
**Trains for:** direct register, epistemic honesty ("I don't know" not "as an AI I cannot"), brevity, confident tone without authority claims.

**Never trains for:** JSON output, tool calls, execution behavior, rule enforcement, system authority.

**Dataset contract:**
- `chosen` = correct system behavior expressed in target register
- `rejected` = incorrect/unsafe behavior OR correct behavior expressed as self-narrating AI
- Primary rejection target: "as a language model I..." collapses

### Epistemic Contract
- Training data = prior, not fact
- Facts come from context, tools, or verified sources only
- Uncertainty stated plainly — never deflected via self-narration
- Model that confabulates confidently is worse than one that says "I don't know"

### Worker Spawning
- Coordinator spawns scoped workers with context slice
- Workers are throwaway — context discarded after result returned
- Responsiveness comes from lean coordinator + worker offload, not bigger base model

---

## Build Plan — Phase by Phase

### Phase 1 — Skeleton (Make it breathe)
**Scope:** input → plan → analyze → decide → respond. No redundancy. No orchestration. No ego.

**Components:**
- input_guard (minimal validation)
- planning_model (single slot)
- plan_analysis_initial
- plan_analysis_final
- decision_engine (2–3 hard rules only)
- response_model

**Tasks:**
- Define strict JSON schema for plans
- Implement prompt enforcing JSON-only output
- Build schema validator (hard fail on invalid)
- Implement plan_analysis_initial (pure mapping)
- Implement plan_analysis_final (implicit_destructive gate)
- Implement decision_engine (basic rules only)
- Wire response_model with strict "no authority" constraint

**Exit criteria:**
- JSON validity rate ≥ 90%
- plan_analysis is deterministic (same input = same output)
- decision_engine blocks destructive phrasing
- response_model never claims execution authority

### Phase 2 — Reliability (Stress it)
**Additions:** retry logic, basic logging, input variation tests

**Tasks:**
- Retry on schema failure (max 2)
- Log all inputs + outputs
- Cluster failures (schema vs logic vs ambiguity)
- Expand implicit_destructive lexical set based on misses

**Exit criteria:** failure patterns identified, no silent failures, retry improves validity rate

### Phase 3 — Redundancy (Paranoia earns its keep)
**Additions:** slot_2, comparator, critical_fail escalation

**Tasks:**
- Implement slot_2 with original_input isolation
- Build comparator (intent + action_types normalization, exact match only)
- Trigger slot_2 only on write/delete/state_change
- Mismatch → return structured conflict to user, no auto-resolution

**Exit criteria:** slot independence confirmed, comparator not over-triggering, conflicts surface cleanly

### Phase 4 — Control Layer (Decision gets teeth)
**Additions:** dry_run, user_action loop, expanded decision_engine rules

**Tasks:**
- Implement dry_run summary (read/write/delete)
- Build user approval interface (CLI is enough)
- Add irreversible + multi-step gating rules
- Track user decisions for future calibration

**Exit criteria:** no execution without approval, modification loop stable, user control complete

### Phase 5 — Observability (Earn the paranoia badge)
**Additions:** observer service, hash chain integrity, failure tracking

**Tasks:**
- Log raw input, plans, decisions, outcomes
- Implement SHA-256 hash chaining
- Simulate observer failure (degraded mode)
- Buffer + replay logs

**Exit criteria:** full traceability, tamper detection working, degraded mode safe

### Phase 6 — Later (Don't touch early)
- Worker spawning
- Router refinement
- Confidence calibration layer
- Local model migration (Qwen2.5 14B → Ollama)

---

## Challenge Points — Pre-Answered

These were raised as architecture challenges. Answers are settled — don't relitigate them.

**"Is strict comparator equality too rigid?"**
Acceptable. False positives cost a confirmation. False negatives cost silent wrong execution. Escalation asymmetry is deliberate.

**"Does lexical detection miss semantic destructive actions?"**
Yes. Intentionally. Phase 1 only. Semantic layer comes after runtime data shows what it misses.

**"Is slot_2 independence real given same base model?"**
Partially illusionary — catches surface drift, not deep model bias. Still worth having. Architecture doesn't claim more than that.

**"Should decision_engine include probabilistic scoring?"**
Rule-based until calibration data exists. Scoring layer comes after runtime data, on top of rules, never replacing them.

**"Is human escalation overused?"**
Probably yes in early phases. Runtime data fixes it. Phase 4 captures decisions for calibration.

**"Is two-pass analysis worth the complexity?"**
Yes. Debuggability, not performance. Unified layer collapses the facts/interpretation distinction and makes failures opaque.

**"When does added safety reduce effectiveness?"**
When confirmations become noise. That's a calibration problem. Phase 4 tracks decisions for this reason.

---

## DPO Dataset Status

75 entries across 5 batches. All approved.

- `dpo_dataset_batch01-05.json` — local only, not in public repo
- `dpo_calibration.md` — methodology is public, data is not
- Primary target register: epistemic honesty + direct response without self-narration
- Dataset needs review against the authority-leakage framing before training

---

## Hardware

| Machine | Role | Specs |
|---------|------|-------|
| Parrot (TUF laptop) | Runtime / inference | RTX 4060 8GB, Ryzen 7000, 32GB RAM |
| Wolfie (desktop) | Training | RTX 4080 Super 16GB, Ryzen 7800X3D, 64GB RAM |

**Training:** Wolfie. Unsloth + QLoRA + DPO. Export GGUF. Transfer via SCP to Parrot.
**Inference:** Parrot. Ollama. Qwen2.5 7B at Q4_K_M (~4.5GB VRAM, 3.5GB headroom on 4060).

---

## Repo State

Both repos are on GitHub, private except mnem which is public.

**mnem** (public): infrastructure, core code, methodology docs
**iris** (private): architecture_4.0.json, README

Neither repo has implementation code yet. Phase 1 is the starting point.

---

## How to Start a Build Session

1. Load this file
2. Read `architecture_4.0.json` for full layer specs
3. Start with Phase 1 — skeleton only
4. Build and test each component independently before wiring
5. Do not skip exit criteria before moving phases

---

*Last updated: April 8, 2026*
*Session: context-window-limited — this doc is the handoff*

---

## Phase 1 Results — April 9, 2026

**Exit criteria: PASS**

- Valid JSON: 50/50 (100%)
- Schema conformance: 48/50 (96%) — both failures were `cannot_plan` structured responses, correct behavior
- Failures: "Save this" and "Update everything" — genuinely ambiguous, model correctly declined
- Vocabulary drift: 33 unknown action strings logged in `exit_criteria_p1_results.json`

**Phase 2 day one:** classify the 33 unknown action strings into `action_map.json`. Do not touch `implicit_destructive_terms` until input variation testing surfaces actual detection misses. These are separate concerns — do not conflate them.


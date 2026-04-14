# IRIS Memory Architecture

**Status:** Conceptual — implementation deferred  
**Last updated:** 2026-04-14  
**Scope:** IRIS-native memory system. No dependency on Mnem infrastructure.

---

## Core Principle

> The system trusts actions, but verifies reality.

Decision is not a trust score. It is a function of three independent axes evaluated against risk:

```
Decision = f(Action Reliability, Target Familiarity, Context Alignment, Risk)
```

No axis collapses into another. No scalar shortcut. Each axis can veto execution independently.

---

## Why Not a Trust Score

A single trust score produces:

> "I've deleted files here many times → this delete is safe"

Which is how you delete the wrong thing with full confidence.

The three-axis model separates:
- **Knowing how** (Action Reliability)
- **Knowing what** (Target Familiarity)
- **Knowing whether this situation matches past experience** (Context Alignment)

These are independent questions. Merging them into one number loses the distinction that matters most.

---

## The Three Axes

### 1. Action Reliability

**Question:** Can the system execute this action correctly?

**What it represents:** Mechanical competence. How reliably does this action type perform under these conditions.

**Built from:**
- Success count
- Failure count
- Failure severity (weighted asymmetrically — see Failure Model)
- Repetition under matching context

**Behavior:**
- Increases with successful repetition
- Decreases with failures, asymmetrically
- Decays slowly over time
- Scoped by context — never global

**Critical constraint — Context Scoping:**

Action reliability is scoped by a deterministic context signature. Trust does not transfer outside this scope.

Context signature format:
```
{action}:{path_pattern}:{constraints}
```

Example:
```
delete:/home/nikuhkid/docs/*:recursive:no_symlinks
```

Signature components:
- **Path pattern** — not always exact path; pattern that covers the target scope
- **Operation type** — delete, write, read, etc.
- **Execution constraints** — recursive, depth limit, symlink handling, filters

**Scope calibration:**
- Too broad → unsafe trust generalization
- Too narrow → memory fragments, everything becomes unique, nothing is learned

The signature must be narrow enough to prevent false trust transfer, broad enough to accumulate meaningful experience.

---

### 2. Target Familiarity

**Question:** Do we know the current state of the target right now?

**What it represents:** Situational awareness. Not whether we've acted here before — whether we know what's actually there today.

**Built from:**
- Last inspection timestamp
- Known structure (file count, dir count, symlink count, total size)
- Structural fingerprint (lightweight snapshot hash)
- Observed volatility

**Behavior:**
- Increases with recent inspection
- Decays fast — environment drift is assumed
- Resets on major detected changes
- Does not accumulate from action history alone — requires active observation

**Decay rationale:**

Decay here is not about time passing. It is about the likelihood that reality has changed. Time is a proxy for environmental drift. A path that was safe to delete last month may be a project root today. Volume of past operations does not compensate for unknown current state.

---

### 3. Context Alignment

**Question:** Is this situation actually comparable to past experience?

**What it represents:** Relevance. Whether the current intent, target, and constraints match the conditions under which past experience was built.

**Built from:**
- Similarity between current intent and past contexts
- Similarity between current target state and past target observations
- Similarity between current constraints and past execution conditions

**Behavior:**
- Computed at runtime — never persisted
- Confidence-based, not binary
- Degrades automatically when Target Familiarity is low

**Critical dependency:**

```
Target Familiarity ↓  →  Context Alignment reliability ↓
```

This dependency is enforced, not optional. If Target Familiarity is below threshold, Context Alignment is set to unreliable regardless of computed similarity. You cannot assess whether a situation matches past experience if you don't know the current state of the target.

```
if target_familiarity < threshold:
    context_alignment = UNRELIABLE
```

---

## Axis Dependency Map

```
Target Familiarity
    ↓ (enables)
Context Alignment reliability

If Target Familiarity = LOW or UNKNOWN:
    Context Alignment = UNRELIABLE (forced, not computed)
    Auto-execute = BLOCKED
```

---

## Decision Gate

The decision engine evaluates all axes plus risk. This is a gate, not a scoring system.

```
IF (
    Action Reliability = HIGH
    AND Target Familiarity = HIGH
    AND Context Alignment = STRONG
    AND Risk = LOW
)
→ auto_execute

ELSE
→ require_confirmation OR block
```

**One miss = no auto.** There is no partial credit.

### Auto-execute examples (all four conditions must hold)
- Read file (known path, recent inspection, matching context, low risk)
- List directory (same)
- Write to designated temp folder (same)

### Never auto-execute
- Delete outside designated safe zones
- Overwrite configuration files
- Any recursive operation on unknown or stale target state
- Any action where any single axis is LOW, UNKNOWN, or UNRELIABLE

---

## Pre-Execution Inspection

### Trigger condition
```
Target Familiarity < threshold
```

### Purpose
The system does not need to relearn how to perform an action. It needs to relearn what it is about to act on. This is target revalidation, not skill warm-up.

### Inspection steps
1. List target contents
2. Check structure against last known state
3. Detect anomalies or unexpected changes
4. Update Target Familiarity

### Inspection outcome — tri-state

| Outcome | Familiarity | Effect |
|---|---|---|
| SUCCESS | Updated | Proceed to Context Alignment |
| FAILURE | UNKNOWN | Auto-execute blocked |
| PARTIAL | DEGRADED | Treat as low confidence, auto-execute blocked |

**No fallback. No best guess.**

### Inspection failure behavior (explicit)

```
Inspection failure → Target Familiarity = UNKNOWN
                  → Context Alignment = UNRELIABLE
                  → Auto-execute = BLOCKED
```

### Failure cases

**Path does not exist:**
- Familiarity = UNKNOWN
- Auto-execute blocked
- Exception: if intent is explicitly CREATE, path non-existence is the expected precondition. Auto-execute still blocked (unknown target state), but the action is not invalid. Decision engine evaluates intent before blocking.

**Permission denied:**
- Familiarity = UNKNOWN
- Must escalate or require confirmation

**Partial inspection (timeout, incomplete listing):**
- Familiarity = DEGRADED
- Treated as low confidence
- Auto-execute blocked

---

## Failure Model

Failures are not equal. Weighting must reflect actual impact.

### Failure weight factors
- **Severity** — data loss > permission error > timeout
- **Scope** — 1 file affected vs 1000 files affected
- **Context mismatch** — action performed on wrong target is worst case

### Asymmetric decay

Failures decay, but not at the same rate as successes age out.

| Failure type | Decay speed |
|---|---|
| Minor (permission error, timeout) | Normal |
| Moderate (partial data loss) | Slow |
| Catastrophic (wrong target, mass deletion) | Very slow |

The system remembers pain longer than it forgets success. A catastrophic failure on a context signature persists as a significant weight against Action Reliability for that scope long after equivalent successes would have normalized.

---

## Decay Model

| Axis | Decay speed | Primary driver |
|---|---|---|
| Action Reliability | Slow | Time + failure events |
| Target Familiarity | Fast | Time (environmental drift assumed) |
| Failures (severe) | Very slow | Severity-weighted, asymmetric |

Decay is continuous, not binary expiration. A score does not hit zero and disappear — it becomes increasingly low confidence until it no longer meets threshold for any auto-execute path.

---

## What Is Stored vs Computed

### Stored (persisted)

**Action experience:**
- Action type
- Context signature (scoped)
- Success count
- Failure count
- Failure severity sum
- Last used timestamp

**Target observations:**
- Path
- Last inspected timestamp
- Known structure (file count, dir count, symlink count, total size)
- Structural snapshot hash (lightweight fingerprint)

**Run history:**
- Full trace of all pipeline runs
- Action, target path, outcome, failure severity per run
- Feeds action experience updates and future analysis

### Not stored (computed at runtime)

- Context Alignment — derived from stored data, never persisted
- Final "trust score" — does not exist

---

## Execution Lifecycle

```
1. Input

2. Plan Analysis (initial)
   → resolve intent
   → identify targets

3. Memory Retrieval
   → fetch action experience (by context signature)
   → fetch target observations (by path)

4. Pre-Execution Check
   IF Target Familiarity < threshold:
       → inspect target
       → update familiarity
       → if inspection fails: familiarity = UNKNOWN, proceed to decision with blocked state

5. Context Alignment (compute)
   IF Target Familiarity < threshold:
       → context alignment = UNRELIABLE (skip computation)
   ELSE:
       → compute similarity against past contexts

6. Decision Engine
   → evaluate Action Reliability + Target Familiarity + Context Alignment + Risk
   → apply gate logic
   → verdict: auto_execute | require_confirmation | block

7. Dry Run Summary + Approval (if require_confirmation)

8. Execution OR block

9. Post-Execution Update
   → update action experience (success/failure, severity)
   → update target observations (if inspection performed)
   → log run to run history
```

---

## Guardrails

### No blind execution
If any axis is UNKNOWN, LOW, or UNRELIABLE → auto-execute is disallowed. No exceptions.

### No trust leakage
High reliability in one context signature does not transfer to another. A different path pattern, constraint set, or operation type is a different signature.

### No forgiveness model
Failures persist. Severe failures persist longer. The system does not recover emotionally from catastrophic actions — it structurally remembers them. There is no forgiveness mechanism.

### No assumption of stability
All targets are assumed to drift over time. Past observations are always treated as potentially stale. Recency of inspection is required for any high-familiarity classification.

### No creative failure handling
Inspection failure does not trigger fallback heuristics or best-guess familiarity estimates. The outcome is always explicit: SUCCESS, FAILURE, or PARTIAL. Ambiguity defaults to the safer state.

---

## What This System Is Not

- Not a scoring system — no single number represents trust
- Not a learning model — no weights, no gradients, no training
- Not globally aware — all axes are scoped, nothing transfers implicitly
- Not forgiving — failure history is structural, not emotional

---

## Design Philosophy

> Build a system that behaves like it has experience, not one that stores everything.

The goal is not to remember everything. The goal is to know:
- What it knows
- What it used to know
- When it cannot know right now

That distinction — between confident knowledge, stale knowledge, and absent knowledge — is what separates a careful operator from a confident liability.

---

## Implementation Notes (deferred)

Schema design and pipeline integration are deferred to the memory implementation phase. This document defines the conceptual contract. Implementation must honour these constraints:

- Three axes must remain independent at the data layer
- Context signature format must be deterministic and consistent
- Inspection tri-state must be explicitly handled — no implicit fallbacks
- Failure severity must be a first-class field, not derived post-hoc
- Run history is the ground truth — all other tables derive from it

Do not over-normalise in v1. Build just enough to support the three axes cleanly. Let real usage surface what is missing.

"""
iris/approval_loop.py

CLI user approval interface.
Renders the dry-run summary and prompts for a decision.

Returns a structured result to the caller — never re-invokes the pipeline.
Re-invocation and depth tracking are the caller's responsibility.

Actions:
    approve  → caller proceeds to execution
    reject   → caller stops, logs decision
    kill     → caller aborts entirely
    modify   → caller re-invokes pipeline with amended_input as a fresh call
"""

VALID_ACTIONS = {"approve", "reject", "kill", "modify"}


def _render(dry_run: dict) -> None:
    """Print a human-readable dry-run summary to stdout."""
    print("\n" + "=" * 60)
    print("  DRY-RUN SUMMARY")
    print("=" * 60)
    print(f"  Intent:     {dry_run['intent']}")
    print(f"  Steps:      {dry_run['step_count']}")

    ops = dry_run["operations"]
    op_parts = []
    if ops["reads"]:    op_parts.append(f"{ops['reads']} read(s)")
    if ops["writes"]:   op_parts.append(f"{ops['writes']} write(s)")
    if ops["deletes"]:  op_parts.append(f"{ops['deletes']} delete(s)")
    if ops["unknowns"]: op_parts.append(f"{ops['unknowns']} unknown(s)")
    print(f"  Operations: {', '.join(op_parts) if op_parts else 'none'}")

    print()
    for i, step in enumerate(dry_run["steps"], 1):
        print(f"  [{i}] {step['action']}")
        print(f"       target: {step['target']}")
        print(f"       risk:   {step['risk']}")

    print()
    print(f"  Verdict: {dry_run['verdict'].upper()}")
    print(f"  Reason:  {dry_run['verdict_reason']}")
    print("=" * 60)


def prompt(dry_run: dict) -> dict:
    """
    Render dry-run summary and prompt user for approval decision.

    Args:
        dry_run: output of dry_run.summarize()

    Returns one of:
        {"action": "approve"}
        {"action": "reject"}
        {"action": "kill"}
        {"action": "modify", "amended_input": str}
    """
    _render(dry_run)

    while True:
        print("\n  approve / modify / reject / kill")
        choice = input("  > ").strip().lower()

        if choice not in VALID_ACTIONS:
            print(f"  Invalid choice: '{choice}'. Enter one of: approve, modify, reject, kill.")
            continue

        if choice == "modify":
            print("\n  Enter amended input:")
            amended = input("  > ").strip()
            if not amended:
                print("  Amended input cannot be empty. Try again.")
                continue
            return {"action": "modify", "amended_input": amended}

        return {"action": choice}

"""CLI run of the full scenario: incident -> resolve -> new session recalls the lesson.

    python scripts/demo.py            # uses LLM_BASE_URL / LLM_MODEL from env
"""
import json
import sys

from blastwave import agents, memory, seed, tools


def emit(e):
    t = e["type"]
    if t == "tool":
        flag = " [guardrail]" if e["guardrail"] else ""
        print(f"   {e['agent']:>12} -> {e['tool']}({json.dumps(e['args'])}){flag}: {len(e['rows'])} rows")
    elif t == "agent_end":
        print(f"   {e['agent']:>12} says: {e['text']}\n")
    elif t == "handoff":
        print(f"   ---- HANDED_OFF {e['from']} -> {e['to']} ----")
    elif t in ("llm_error", "tool_error", "error"):
        print("   !!", e)


if __name__ == "__main__":
    if "--no-reseed" not in sys.argv:
        seed.main()
    print("\n== Session 1: checkout-service is on fire")
    out = agents.run_incident("acme", "checkout-service", "checkout-service 5xx rate 18% (SLO 1%)", emit,
                              alert_id="ALERT-7731")
    print(json.dumps(out["state"], indent=1))
    print("\n== Human resolves; graph learns")
    print(memory.resolve_incident("acme", out["incident"], "dev",
                                  "Rolled back ledger-service to v2.13.2\nSet DB_POOL_MAX=50 in config-service",
                                  memory.start_session("acme", "resolution")))
    print("\n== Session 2 (new session): what does the graph remember about ledger-service?")
    for r in tools.recall_memory("acme", "ledger-service").rows[:4]:
        print("  -", r["agent"], ":", (r["text"] or "")[:140])

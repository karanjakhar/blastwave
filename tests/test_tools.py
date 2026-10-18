"""Run against a live FalkorDB (docker compose up -d falkordb). Reseeds the demo tenants."""
import pytest

from blastwave import agents, memory, seed, tools


@pytest.fixture(scope="module", autouse=True)
def seeded():
    seed.main()


def names(rows, key="service"):
    return {r[key] for r in rows}


def test_blast_radius_is_multi_hop():
    rows = tools.blast_radius("acme", "payments-db").rows
    assert {"ledger-service", "payments-api", "checkout-service", "api-gateway", "web-frontend"} <= names(rows)
    assert {r["service"]: r["hops"] for r in rows}["checkout-service"] == 3


def test_root_cause_found_two_hops_downstream():
    top = tools.recent_deploys_upstream("acme", "checkout-service").rows[0]
    assert top["deploy"] == "ledger-service@v2.14.0"
    assert top["via"] == ["checkout-service", "payments-api", "ledger-service"]
    assert top["hops"] == 2


def test_shortest_path():
    assert tools.path_between("acme", "web-frontend", "payments-db").rows[0]["hops"] == 5


def test_algorithms():
    assert len(tools.critical_services("acme", 5).rows) == 5
    assert "payments-api" in names(tools.chokepoints("acme", 3).rows)
    assert tools.service_clusters("acme").rows


def test_similar_incident_and_procedure():
    assert tools.similar_past_incidents("acme", "checkout-service").rows[0]["incident"] == "INC-1042"
    assert tools.procedures_for("acme", "checkout-service").rows[0]["procedure"] == "PROC-1"


def test_company_brain():
    page = tools.who_to_page("acme", "ledger-service").rows
    assert page[0]["handle"] == "dev" and page[0]["manager"] == "aisha"
    assert "priya" in {r["handle"] for r in page if r["reason"] == "expert"}
    srcs = {s["id"] for s in tools.decision_trace("acme", "ledger-service").rows[0]["sources"]}
    assert {"DOC-ADR-17", "DOC-PM-1042", "PAY-311"} <= srcs


def test_tenant_isolation():
    assert tools.blast_radius("globex", "payments-db").rows == []
    assert names(tools.blast_radius("globex", "volcano-db").rows) == {"doomsday-api"}


def test_incident_pipeline_and_cross_session_memory(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9/v1")  # force guardrail path: deterministic
    events = []
    out = agents.run_incident("acme", "checkout-service", "5xx", events.append, alert_id="ALERT-7731")
    st = out["state"]
    assert st["severity"] == "SEV1" and st["suspected_deploy"] == "ledger-service@v2.14.0" and "dev" in st["paged"]
    assert [e["from"] for e in events if e["type"] == "handoff"] == ["triage", "investigator"]

    res = memory.resolve_incident("acme", out["incident"], "dev", "Rolled back ledger\nPinned pool", memory.start_session("acme", "t"))
    assert res["procedure"] == "PROC-1"
    # A brand-new session recalls what the previous one learned.
    recalled = tools.recall_memory("acme", "ledger-service").rows
    assert any(out["incident"] in (r["text"] or "") for r in recalled)
    assert tools.procedures_for("acme", "checkout-service").rows[0]["uses"] == 2

"""Record a captioned walkthrough of the running app (needs server on :8080 + LLM).

    python scripts/record_demo.py out_dir [--chrome /usr/bin/google-chrome]
Produces out_dir/*.webm; convert with ffmpeg -i in.webm -c:v libx264 -pix_fmt yuv420p demo.mp4
"""
import asyncio
import sys

from playwright.async_api import async_playwright

W, H = 1600, 900
CAPTION_JS = """(t) => {
  let el = document.getElementById('__cap');
  if (!el) { el = document.createElement('div'); el.id='__cap';
    el.style.cssText = 'position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:9999;max-width:1100px;'+
      'background:rgba(10,12,18,.92);color:#fff;border:1px solid #8b7cff;border-radius:12px;padding:12px 20px;'+
      'font:600 19px/1.4 Inter,system-ui,sans-serif;text-align:center;box-shadow:0 8px 30px #0008;transition:opacity .3s';
    document.body.appendChild(el); }
  el.style.opacity = t ? 1 : 0; el.innerHTML = t || '';
}"""


async def main(out_dir: str, chrome: str | None):
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=chrome) if chrome else await p.chromium.launch()
        ctx = await b.new_context(viewport={"width": W, "height": H}, record_video_dir=out_dir,
                                  record_video_size={"width": W, "height": H})
        pg = await ctx.new_page()

        async def cap(text, wait=0.0):
            await pg.evaluate(CAPTION_JS, text)
            if wait:
                await pg.wait_for_timeout(int(wait * 1000))

        async def click_step(tool, nth=0, caption=None, wait=4.5):
            steps = pg.locator(".step", has=pg.locator(f"b:text-is('{tool}')"))
            if await steps.count() > nth:
                el = steps.nth(nth)
                await el.scroll_into_view_if_needed()
                await el.click()
                if caption:
                    await cap(caption)
                await pg.wait_for_timeout(int(wait * 1000))
                await el.click()  # collapse again

        await pg.request.post("http://localhost:8080/api/reset")
        await pg.goto("http://localhost:8080/")
        await pg.wait_for_timeout(2500)
        await cap("<b>Blastwave</b> — on-call agents that use <span style='color:#ff5fa2'>FalkorDB</span> "
                  "as their context, memory and shared state", 4)
        await cap("The graph: 29 services, teams, people, deploys, incidents, runbooks, ADRs, tickets & decisions", 4)
        await cap("🔥 checkout-service is throwing 5xx. Three agents take it: triage → investigator → comms", 3)
        await pg.click("#alerts button")
        await cap("Triage: multi-hop blast radius + PageRank → sets severity on the shared Incident node", 0)
        await pg.wait_for_selector("text=investigator agent started", timeout=300000)
        await cap("Handoff is a HANDED_OFF edge. The investigator only gets the incident id — it reads state from the graph")
        await pg.wait_for_selector("text=comms agent started", timeout=300000)
        await cap("Comms: who to page = on-call + people with real history (tickets, deploys, resolved incidents)")
        await pg.wait_for_selector("text=Done.", timeout=300000)
        await pg.wait_for_timeout(1500)
        await cap("Every step is traceable: click it → exact Cypher, rows, and the subgraph it used", 3)
        await click_step("blast_radius", caption="blast_radius: (:Service)-[:DEPENDS_ON*1..6]->(checkout) — who breaks upstream")
        await click_step("recent_deploys_upstream",
                         caption="Root cause is 2 hops DOWNSTREAM: checkout → payments-api → ledger-service@v2.14.0 "
                                 "(pool 50→10, 38 min ago)", wait=6)
        await click_step("similar_past_incidents", caption="Episodic memory: INC-1042 — same failure in March, resolved by @priya")
        await click_step("procedures_for", caption="Procedural memory: PROC-1, the fix recorded last time")
        await click_step("page", nth=0, caption="Action: page @dev (on-call)…")
        await click_step("page", nth=1, caption="…and @priya, because she resolved INC-1042 — evidence from the graph")
        await cap("Shared incident state lives on the Incident node — any agent or human can resume it", 4)

        await pg.click("#resolve")
        await pg.wait_for_timeout(800)
        await cap("Resolve → the fix becomes procedural memory (PROC-1 uses+1, LEARNED_FROM this incident)", 4)
        await pg.click(".tabs button[data-t='mem']")
        await pg.wait_for_timeout(800)
        await cap("Memory view: procedures, agent handoffs, facts and sessions — all nodes & edges in FalkorDB", 5)

        await pg.click(".tabs button[data-t='ask']")
        await pg.fill("#q", "Why must the ledger pool be at least 50? Show sources.")
        await cap("Company brain: ask anything. A graph entity-linker grounds mentions to real nodes first", 0)
        await pg.click("#askBtn")
        await pg.wait_for_function("!document.querySelector('#askBtn').disabled", timeout=300000)
        await click_step("decision_trace", caption="Decision trace: DEC-1 ← ADR-17, Postmortem INC-1042, ticket PAY-311 (decided by @aisha)", wait=6)
        await pg.fill("#q", "Who should I call about it?")
        await cap("Follow-up — conversation history is loaded back from Episode nodes in the graph", 0)
        await pg.click("#askBtn")
        await pg.wait_for_function("!document.querySelector('#askBtn').disabled", timeout=300000)
        await pg.wait_for_timeout(3500)

        await pg.click(".tabs button[data-t='inc']")
        await pg.select_option("#tenant", "globex")
        await pg.wait_for_timeout(1500)
        await cap("Multi-tenant: one graph per tenant in a single FalkorDB instance — Globex sees none of Acme", 4)
        await pg.select_option("#tenant", "acme")
        await pg.wait_for_timeout(1500)
        await cap("Runs fully local: FalkorDB + Qwen3-4B on an 8 GB laptop GPU · same 19 tools exposed over MCP", 4)
        await cap("<b>Blastwave</b> · graph-shaped decisions you can trace · github.com/… ", 3)
        await ctx.close()
        await b.close()


if __name__ == "__main__":
    chrome = sys.argv[sys.argv.index("--chrome") + 1] if "--chrome" in sys.argv else None
    asyncio.run(main(sys.argv[1], chrome))

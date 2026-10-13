"""RingBreaker MCP server: exposes the graph tools (graph/tools.py) over the Model Context Protocol.

Run it standalone (stdio):        python -m mcp_server.server
Or over HTTP for other clients:   python -m mcp_server.server --http --port 8765

Any MCP client (Claude Desktop, Claude Code, our own agents) can list these tools and pick which to call.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.mcpserver import MCPServer  # noqa: E402  (mcp>=2: FastMCP was renamed MCPServer)

from graph.tools import TOOLS, TRACK  # noqa: E402

INSTRUCTIONS = """RingBreaker: scam-ring investigation tools over a FalkorDB graph of a bank's accounts, UPI handles,
phones, devices, IPs, links, transactions, scam reports, agent memory and the fraud team's knowledge.
Every tool returns {result, evidence:{nodes, edges}, cypher}. Only state facts that tools return,
and cite node ids. Typical flow for a report: lookup_entity -> expand_neighbors -> find_shared_infrastructure
-> detect_ring -> rank_ring_members -> money_trail -> risk_score -> freeze_accounts / flag_for_review
-> send_alert -> draft_complaint."""

server = MCPServer(name="ringbreaker", instructions=INSTRUCTIONS, version="1.0.0")

for name, fn in TOOLS.items():
    server.add_tool(fn, name=name, description=f"[Track {TRACK[name]}] {(fn.__doc__ or '').strip()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if args.http:
        server.run("streamable-http", port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()

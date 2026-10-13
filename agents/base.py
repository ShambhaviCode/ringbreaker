"""Shared bits for all agents."""
from __future__ import annotations

from dataclasses import dataclass, field

from agents.llm import LLM
from agents.mcp_client import BaseToolClient, Trace, get_client

GROUNDING_RULE = ("Only state facts returned by tools. Cite node ids (accounts, devices, cases, policies, documents) "
                  "and the path for every claim. If the graph has no answer, say so. Never invent ids or amounts.")


@dataclass
class AgentContext:
    tenant: str
    client: BaseToolClient
    trace: Trace = field(default_factory=Trace)
    llm: LLM = field(default_factory=LLM)

    @classmethod
    def create(cls, tenant: str | None = None, transport: str | None = None, llm: LLM | None = None,
               client: BaseToolClient | None = None) -> "AgentContext":
        c = client or get_client(tenant, transport)
        return cls(tenant=c.tenant, client=c, llm=llm or LLM())


class Agent:
    agent_id = "AGT-?"

    def __init__(self, ctx: AgentContext):
        self.ctx = ctx

    def tool(self, name: str, **args) -> dict:
        """Call a graph tool through MCP (or locally) and return its `result`."""
        out = self.ctx.client.call(name, args, agent=self.agent_id, trace=self.ctx.trace)
        return out.get("result", out)

    def say(self, text: str):
        self.ctx.trace.note(self.agent_id, text)

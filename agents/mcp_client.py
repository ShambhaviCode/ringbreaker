"""How agents reach the graph tools.

MCPToolClient  - starts our MCP server as a subprocess (stdio) and keeps one session open in a background
                 thread, so synchronous agent code (and Streamlit) can call tools. This is the default:
                 every tool call an agent makes really goes through MCP.
LocalToolClient - calls graph/tools.py directly (TOOL_TRANSPORT=local), handy for tests.

Both record every call in a Trace: which agent, which tool, the arguments, the Cypher it ran and the evidence.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field

import config


@dataclass
class TraceEvent:
    agent: str
    tool: str
    args: dict
    result: dict
    cypher: list
    evidence: dict
    ms: int
    ts: float = field(default_factory=time.time)


class Trace:
    def __init__(self):
        self.events: list[TraceEvent] = []
        self.notes: list[dict] = []  # agent messages / reasoning lines
        self.lock = threading.Lock()

    def add(self, ev: TraceEvent):
        with self.lock:
            self.events.append(ev)

    def note(self, agent: str, text: str):
        with self.lock:
            self.notes.append({"agent": agent, "text": text, "ts": time.time()})

    def merged_evidence(self) -> dict:
        nodes, edges = {}, {}
        for ev in self.events:
            for n in ev.evidence.get("nodes", []):
                nodes.setdefault(n["id"], {}).update(n)
            for e in ev.evidence.get("edges", []):
                edges[(e["src"], e["dst"], e["type"])] = e
        return {"nodes": list(nodes.values()), "edges": list(edges.values())}


class BaseToolClient:
    transport = "base"

    def __init__(self, tenant: str | None = None):
        self.tenant = tenant or config.DEFAULT_TENANT

    def _raw_call(self, name: str, args: dict) -> dict:
        raise NotImplementedError

    def list_tools(self) -> list[dict]:
        raise NotImplementedError

    def call(self, name: str, args: dict | None = None, agent: str = "?", trace: Trace | None = None) -> dict:
        args = dict(args or {})
        args.setdefault("tenant", self.tenant)
        t0 = time.time()
        out = self._raw_call(name, args)
        if trace is not None:
            trace.add(TraceEvent(agent=agent, tool=name, args={k: v for k, v in args.items() if k != "tenant"},
                                 result=out.get("result", out), cypher=out.get("cypher", []),
                                 evidence=out.get("evidence", {}), ms=int((time.time() - t0) * 1000)))
        return out


class LocalToolClient(BaseToolClient):
    transport = "local"

    def _raw_call(self, name, args):
        from graph.tools import TOOLS

        return TOOLS[name](**args)

    def list_tools(self):
        from mcp_server.server import server

        tools = asyncio.run(server.list_tools())
        return [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]


class MCPToolClient(BaseToolClient):
    """One long-lived MCP stdio session, driven from a background event-loop thread."""

    transport = "mcp"

    def __init__(self, tenant: str | None = None):
        super().__init__(tenant)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._ready: Future = Future()
        self._stop = None
        self._session = None
        asyncio.run_coroutine_threadsafe(self._run(), self._loop)
        self._ready.result(timeout=60)

    async def _run(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=sys.executable, args=["-m", "mcp_server.server"], cwd=str(config.ROOT),
            env={**os.environ, "PYTHONPATH": str(config.ROOT)})
        self._stop = asyncio.Event()
        try:
            with open(os.devnull, "w") as devnull:
                async with stdio_client(params, errlog=devnull) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self._session = session
                        self._ready.set_result(True)
                        await self._stop.wait()
        except BaseException as ex:  # surface startup errors to the caller
            if not self._ready.done():
                self._ready.set_exception(ex)

    def _sync(self, coro, timeout=120):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=timeout)

    def list_tools(self):
        res = self._sync(self._session.list_tools())
        return [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in res.tools]

    def _raw_call(self, name, args):
        res = self._sync(self._session.call_tool(name, args))
        if getattr(res, "is_error", False) or getattr(res, "isError", False):
            text = " ".join(getattr(c, "text", "") for c in res.content)
            return {"tool": name, "result": {"error": text}, "evidence": {}, "cypher": []}
        sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
        if isinstance(sc, dict):
            return sc.get("result", sc) if set(sc) == {"result"} and isinstance(sc.get("result"), dict) \
                and "tool" in sc["result"] else sc
        for c in res.content:
            if getattr(c, "text", None):
                try:
                    return json.loads(c.text)
                except json.JSONDecodeError:
                    return {"tool": name, "result": {"text": c.text}, "evidence": {}, "cypher": []}
        return {"tool": name, "result": {}, "evidence": {}, "cypher": []}

    def close(self):
        if self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)


_clients: dict = {}
_lock = threading.Lock()


def get_client(tenant: str | None = None, transport: str | None = None) -> BaseToolClient:
    """Shared client per (transport, tenant). MCP calls are serialised per session by the SDK; for heavy
    concurrency use one client per agent thread (see tests/test_concurrency.py)."""
    transport = (transport or config.TOOL_TRANSPORT).lower()
    tenant = tenant or config.DEFAULT_TENANT
    key = (transport, tenant)
    with _lock:
        if key not in _clients:
            _clients[key] = MCPToolClient(tenant) if transport == "mcp" else LocalToolClient(tenant)
        return _clients[key]

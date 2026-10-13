"""One small wrapper over LLM providers, with a provider-agnostic tool-calling loop.

LLM_PROVIDER in .env:
  anthropic - Anthropic API (LLM_API_KEY or ANTHROPIC_API_KEY, LLM_MODEL e.g. claude-haiku-5-5)
  openai    - any OpenAI-compatible endpoint (LLM_BASE_URL + LLM_API_KEY + LLM_MODEL): OpenAI, Groq,
              Together, OpenRouter, Ollama (http://localhost:11434/v1), Gemini's OpenAI-compatible API...
  none      - no LLM. Agents run their graph playbooks deterministically (same tools, same MCP calls).
  mock      - scripted responses for tests.
"""
from __future__ import annotations

import json
from typing import Callable

import config

MAX_TOOL_RESULT_CHARS = 6000


class LLM:
    def __init__(self, provider: str | None = None, model: str | None = None, script: list | None = None):
        self.provider = (provider or config.LLM_PROVIDER or "none").lower()
        self.model = model or config.LLM_MODEL
        self._client = None
        self._script = list(script or [])  # for provider="mock"
        if self.provider == "anthropic":
            import anthropic

            kw = {"api_key": config.LLM_API_KEY} if config.LLM_API_KEY else {}
            if config.LLM_BASE_URL:
                kw["base_url"] = config.LLM_BASE_URL
            self._client = anthropic.Anthropic(**kw)
            self.model = self.model or "claude-haiku-5-5"
        elif self.provider == "openai":
            import openai

            self._client = openai.OpenAI(api_key=config.LLM_API_KEY or "not-needed", base_url=config.LLM_BASE_URL)
            self.model = self.model or "gpt-4o-mini"

    @property
    def available(self) -> bool:
        return self.provider in ("anthropic", "openai", "mock")

    # ------------------------------------------------------------------ plain completion
    def complete(self, system: str, user: str, max_tokens: int = 800) -> str:
        if self.provider == "mock":
            return self._script.pop(0) if self._script else ""
        if self.provider == "anthropic":
            r = self._client.messages.create(model=self.model, system=system, max_tokens=max_tokens,
                                             messages=[{"role": "user", "content": user}])
            return "".join(b.text for b in r.content if b.type == "text")
        if self.provider == "openai":
            r = self._client.chat.completions.create(
                model=self.model, max_tokens=max_tokens,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
            return r.choices[0].message.content or ""
        raise RuntimeError("No LLM configured (LLM_PROVIDER=none)")

    def complete_json(self, system: str, user: str) -> dict:
        text = self.complete(system + "\nReply with a single JSON object only.", user)
        start, end = text.find("{"), text.rfind("}")
        try:
            return json.loads(text[start:end + 1]) if start >= 0 else {}
        except json.JSONDecodeError:
            return {}

    # ------------------------------------------------------------------ tool loop
    def run_tools(self, system: str, user: str, tools: list[dict], call_tool: Callable[[str, dict], dict],
                  max_steps: int = 14, on_text: Callable[[str], None] | None = None) -> str:
        """Let the model pick tools (from the MCP tool list) until it answers. Returns the final text.
        call_tool(name, args) must return the tool's JSON result."""
        if self.provider == "mock":
            return self._mock_loop(call_tool, on_text)
        if self.provider == "anthropic":
            return self._anthropic_loop(system, user, tools, call_tool, max_steps, on_text)
        if self.provider == "openai":
            return self._openai_loop(system, user, tools, call_tool, max_steps, on_text)
        raise RuntimeError("No LLM configured (LLM_PROVIDER=none)")

    @staticmethod
    def _compact(out: dict) -> str:
        res = out.get("result", out) if isinstance(out, dict) else out
        text = json.dumps(res, default=str)
        return text if len(text) <= MAX_TOOL_RESULT_CHARS else text[:MAX_TOOL_RESULT_CHARS] + "...(truncated)"

    def _anthropic_loop(self, system, user, tools, call_tool, max_steps, on_text):
        spec = [{"name": t["name"], "description": t["description"][:1000], "input_schema": t["input_schema"]}
                for t in tools]
        messages = [{"role": "user", "content": user}]
        final = ""
        for _ in range(max_steps):
            r = self._client.messages.create(model=self.model, system=system, max_tokens=1500, tools=spec,
                                             messages=messages)
            messages.append({"role": "assistant", "content": [b.model_dump() for b in r.content]})
            texts = [b.text for b in r.content if b.type == "text" and b.text.strip()]
            for t in texts:
                if on_text:
                    on_text(t)
            final = "\n".join(texts) or final
            uses = [b for b in r.content if b.type == "tool_use"]
            if not uses:
                return final
            results = []
            for b in uses:
                out = call_tool(b.name, dict(b.input or {}))
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": self._compact(out)})
            messages.append({"role": "user", "content": results})
        return final

    def _openai_loop(self, system, user, tools, call_tool, max_steps, on_text):
        spec = [{"type": "function", "function": {"name": t["name"], "description": t["description"][:1000],
                                                  "parameters": t["input_schema"]}} for t in tools]
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        final = ""
        for _ in range(max_steps):
            r = self._client.chat.completions.create(model=self.model, messages=messages, tools=spec,
                                                     max_tokens=1500)
            msg = r.choices[0].message
            if msg.content and on_text:
                on_text(msg.content)
            final = msg.content or final
            calls = msg.tool_calls or []
            messages.append({"role": "assistant", "content": msg.content or "",
                             **({"tool_calls": [c.model_dump() for c in calls]} if calls else {})})
            if not calls:
                return final
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                out = call_tool(c.function.name, args)
                messages.append({"role": "tool", "tool_call_id": c.id, "content": self._compact(out)})
        return final

    def _mock_loop(self, call_tool, on_text):
        """Script items: {"tool": name, "args": {...}} or {"text": "..."} (final answer)."""
        final = ""
        while self._script:
            step = self._script.pop(0)
            if "tool" in step:
                call_tool(step["tool"], step.get("args", {}))
            else:
                final = step.get("text", "")
                if on_text:
                    on_text(final)
                break
        return final

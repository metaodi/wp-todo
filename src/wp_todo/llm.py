"""Talking to a model, on a leash.

This is the only module in the project that costs money to run, and the only
one whose answers are not a function of the inputs. Both facts shape it:

* **Every exchange is cached**, keyed by everything that could change the
  answer - model, effort, system prompt, user prompt, schema, tool set. A
  rerun without ``--refresh`` replays the cache and produces a byte-identical
  dossier, which is what "reproducibility by replay" in CLAUDE.md means. Tests
  run with ``offline=True``, where a cache miss raises instead of quietly
  spending money.

* **Every exchange is counted**, against a hard ceiling. The ceiling is not a
  suggestion the model can talk its way past: `LlmBudget.take` is asked before
  each call, and when it says no the run stops and says so in the dossier and
  the transcript. Silently doing less work than the reader expects is the same
  lie as a silently empty section.

Nothing here decides whether an answer is *true*. That is `agent.py`'s five
gates, applied in code after the model has spoken, and the model's own
confidence is an input to ordering there - never a pass condition.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from ._http import OfflineCacheMissError
from .cache import ResponseCache, cache_key

log = logging.getLogger(__name__)

#: The default. Overridable in `[research]`, but not per call: a dossier built
#: from two different models would be harder to reason about than it is worth.
DEFAULT_MODEL = "claude-opus-5"

#: Anthropic's server-side web search. Used for *discovery only* - the URLs are
#: read out of the structured result blocks and then fetched by our own
#: `WebClient`, so every document a finding rests on exists in our cache and
#: can be checked by the quote gate.
WEB_SEARCH_TOOL = "web_search_20260209"

#: Enough for a structured answer with a quote in it, and nowhere near enough
#: for the model to start writing article prose.
MAX_TOKENS = 4_000


class LlmBudgetExceededError(RuntimeError):
    """A call was made after the budget was spent.

    Asking `LlmBudget.take` first is the supported way to run out; reaching
    this exception means somebody skipped that check, which is a bug rather
    than an operating condition.
    """


class LlmUnavailableError(RuntimeError):
    """No API key, or the SDK is not installed."""


@dataclass
class LlmBudget:
    """A hard ceiling on model calls per run.

    Deliberately *asked*, not enforced by exception in the normal path. A
    ceiling that raises would throw away the findings already paid for, which
    is strictly worse than stopping and reporting - so the agent takes
    permission before each call and records the refusal.
    """

    limit: int
    spent: int = 0
    #: Set when a call was refused, so the artefacts can say so.
    exhausted: bool = False

    @property
    def left(self) -> int:
        return max(0, self.limit - self.spent)

    def take(self, purpose: str = "") -> bool:
        if self.spent >= self.limit:
            return self.refuse(purpose)
        self.spent += 1
        return True

    def refuse(self, purpose: str = "") -> bool:
        """Record that the ceiling stopped a call that was going to be made.

        A caller that checks `left` before building a request never reaches
        `take`, so without this the run would do less work than the agenda
        asked for and report `budget_exhausted = False` - a clean-looking run
        that quietly skipped things, which is the failure this whole class
        exists to prevent. Always returns False, so it reads as a refusal at
        the call site.
        """
        self.exhausted = True
        log.warning("model budget of %d call(s) is spent; skipping %s", self.limit, purpose or "a call")
        return False


@dataclass
class LlmCall:
    """One exchange, recorded for the transcript.

    The transcript is committed next to the dossier, so this holds what a
    reader needs to judge the machine's work: what it was asked, what came
    back, and whether the answer was paid for or replayed.
    """

    ordinal: int
    purpose: str
    subject: str
    prompt: str
    reply: dict[str, Any]
    cached: bool
    #: URLs read out of `web_search_tool_result` blocks - structured API data,
    #: never the model's prose. A hallucinated URL cannot enter here.
    found_urls: tuple[str, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    #: Set when the model declined; the agent treats it as "no answer".
    refused: str = ""


@dataclass
class LlmClient:
    """A small, cached, budgeted wrapper around the Messages API."""

    model: str = DEFAULT_MODEL
    effort: str = "medium"
    cache: ResponseCache | None = None
    budget: LlmBudget = field(default_factory=lambda: LlmBudget(limit=10))
    offline: bool = False
    dry_run: bool = False
    api_key: str | None = None
    calls: list[LlmCall] = field(default_factory=list)
    _client: Any = field(default=None, init=False, repr=False)

    # ---------------------------------------------------------------- public
    def ask(
        self,
        *,
        purpose: str,
        subject: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        context: str = "",
        web_search: bool = False,
    ) -> LlmCall | None:
        """One structured question. Returns None when nothing was asked.

        None means the budget refused, or a dry run, or the model declined -
        all of which the caller must treat as "no answer", never as "nothing
        to report".

        `context` is the bulky, unchanging part - the document excerpts every
        question in a run shares. It is sent as the first block of the user
        turn and carries the cache breakpoint, because prompt caching is a
        prefix match and the volatile half must come last.
        """
        tools = self._tools(web_search)
        key = cache_key(
            "LLM",
            self.model,
            {
                "effort": self.effort,
                "system": system,
                "context": context,
                "prompt": prompt,
                "schema": json.dumps(schema, sort_keys=True, ensure_ascii=False),
                "tools": json.dumps(tools, sort_keys=True, ensure_ascii=False),
            },
        )

        if self.cache is not None:
            cached = self.cache.get(key)
            if cached is not None:
                # A replay is free, so it does not touch the budget: the
                # ceiling is on spending, not on work done.
                return self._record(purpose, subject, prompt, cached, cached_hit=True)

        if self.offline:
            raise OfflineCacheMissError(f"no recorded model reply for {purpose}/{subject}")

        if self.dry_run:
            log.info("dry-run: would ask the model about %s (%s)", subject, purpose)
            return None

        if not self.budget.take(f"{purpose}/{subject}"):
            return None

        payload = self._send(system, context, prompt, schema, tools)
        if self.cache is not None:
            self.cache.put(key, payload)
        return self._record(purpose, subject, prompt, payload, cached_hit=False)

    # ----------------------------------------------------------------- core
    def _tools(self, web_search: bool) -> list[dict[str, Any]]:
        if not web_search:
            return []
        return [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": 5}]

    def _send(
        self,
        system: str,
        context: str,
        prompt: str,
        schema: dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """The one place a request actually leaves the machine.

        `context` is text fetched from hosts nobody here controls, so it is
        sent as user content and never as part of `system`. It used to go in
        the system prompt, which is the highest-trust channel in the request -
        the wrong place for an unvetted page, however narrow the blast radius
        is with no tool the model can reach.

        Narrow is not nil, and it is worth being exact about what the gates do
        and do not cover: a hostile page can carry both an instruction and a
        verbatim sentence that satisfies the quote gate, because the gate
        checks the sentence is *on the page*, not that the page is honest.
        What it guarantees is that the quote really is at the URL shown, which
        is what makes rule 1 of docs/research-policy.md - check every finding
        at its source - something a reader can actually carry out.
        """
        client = self._sdk()
        content: list[dict[str, Any]] = []
        if context:
            # The breakpoint goes at the end of the stable half. Everything a
            # per-claim question varies lives in `prompt`, after it.
            content.append({"type": "text", "text": context, "cache_control": {"type": "ephemeral"}})
        content.append({"type": "text", "text": prompt})
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": MAX_TOKENS,
            "system": [{"type": "text", "text": system}],
            "messages": [{"role": "user", "content": content}],
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        if tools:
            kwargs["tools"] = tools

        response = client.messages.create(**kwargs)
        return _envelope(response)

    def _sdk(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised by operators, not CI
            raise LlmUnavailableError(
                "the research agent needs the `anthropic` package: `uv sync --extra agent`"
            ) from exc
        try:
            self._client = (
                anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()
            )
        except Exception as exc:  # pragma: no cover - depends on the environment
            raise LlmUnavailableError(
                "no Anthropic credentials: set ANTHROPIC_API_KEY, or run without --agent"
            ) from exc
        return self._client

    def _record(
        self, purpose: str, subject: str, prompt: str, payload: dict[str, Any], *, cached_hit: bool
    ) -> LlmCall | None:
        refusal = str(payload.get("refusal", ""))
        raw = payload.get("json")
        reply = raw if isinstance(raw, dict) else {}
        if not reply and not refusal:
            log.warning("%s/%s: the model returned nothing usable", purpose, subject)

        call = LlmCall(
            ordinal=len(self.calls) + 1,
            purpose=purpose,
            subject=subject,
            prompt=prompt,
            reply=reply,
            cached=cached_hit,
            found_urls=tuple(str(u) for u in payload.get("urls", []) or []),
            input_tokens=int(payload.get("input_tokens", 0) or 0),
            output_tokens=int(payload.get("output_tokens", 0) or 0),
            refused=refusal,
        )
        self.calls.append(call)
        if refusal:
            log.warning("%s/%s: the model declined (%s)", purpose, subject, refusal)
            return None
        return call


def _envelope(response: Any) -> dict[str, Any]:
    """The parts of a Messages response worth storing, as plain JSON.

    Stored rather than the whole response object so the cache stays greppable
    and a replay does not depend on an SDK version.
    """
    if getattr(response, "stop_reason", "") == "refusal":
        details = getattr(response, "stop_details", None)
        return {"refusal": str(getattr(details, "category", "") or "refusal")}

    text = ""
    urls: list[str] = []
    for block in getattr(response, "content", []) or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            text += getattr(block, "text", "")
        elif kind == "web_search_tool_result":
            urls.extend(_result_urls(block))

    usage = getattr(response, "usage", None)
    return {
        "json": _loads(text),
        "urls": _dedupe(urls),
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
    }


def _result_urls(block: Any) -> list[str]:
    """URLs out of a search result block, which is structured API data.

    This is the whole reason discovery goes through the server tool rather than
    asking the model to name sources: a URL that came from here was returned by
    a search engine, so it cannot have been invented mid-sentence. An error
    block carries an object rather than a list - see the SDK's note about
    server-tool errors arriving as HTTP 200.
    """
    content = getattr(block, "content", None)
    if not isinstance(content, list):
        return []
    found: list[str] = []
    for item in content:
        url = getattr(item, "url", None)
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            found.append(url)
    return found


def _dedupe(urls: list[str]) -> list[str]:
    """First occurrence wins, order preserved: search rank is information."""
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def _loads(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except ValueError:
        log.warning("the model's reply was not JSON despite a schema being set")
        return {}
    return parsed if isinstance(parsed, dict) else {}

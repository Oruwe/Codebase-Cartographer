"""OpenRouter egress - the security boundary of the whole project.

This is the one place where private source code can leave the machine, so:
  * It is OFF by default. The local graph pipeline never imports `requests`.
  * Nothing is sent until a payload has been built, redacted and (by default)
    printed for inspection. Dry-run is the default even once egress is enabled.
  * Token and spend ceilings are enforced here in Python, not requested in a prompt.

The request shape below follows the OpenRouter chat-completions API, which is
OpenAI-compatible: POST {base_url}/chat/completions with {model, messages, ...}.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .config import EgressPolicy
from .graph import Graph
from .obs import NULL_LOGGER, Logger
from .query import QueryResult
from .redact import redact_obj, redact_text

# Rough characters-per-token used only for the *ceiling* check. It intentionally
# over-estimates tokens so the cap trips early rather than late.
CHARS_PER_TOKEN = 3.5

PAYLOAD_FORMAT = "orgono-openrouter-chat/1"


class EgressRefused(PermissionError):
    """Raised when a request is blocked by policy before any socket is opened."""


@dataclass
class EgressPlan:
    """What *would* be sent. Inspectable before anything leaves the machine."""

    url: str
    model: str
    headers: dict[str, str]
    body: dict[str, Any]
    estimated_prompt_tokens: int
    estimated_cost_usd: float
    redactions: int
    format: str = PAYLOAD_FORMAT
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "format": self.format,
            "url": self.url,
            "model": self.model,
            "headers": self.headers,
            "body": self.body,
            "estimated_prompt_tokens": self.estimated_prompt_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "redactions": self.redactions,
            "notes": self.notes,
        }

    def pretty(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def _estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def build_plan(
    question: str,
    result: QueryResult,
    graph: Graph,
    policy: EgressPolicy,
    api_key: str | None = None,
    log: Logger | None = None,
) -> EgressPlan:
    """Build the exact request that would be sent. Never opens a socket."""
    log = log or NULL_LOGGER
    redactions = 0

    context_lines: list[str] = []
    for node in result.nodes:
        context_lines.append(
            f"{node['kind']} {node['name']} ({node['path']}:{node['start_line']})"
        )
    for edge in result.edges[:200]:
        context_lines.append(
            f"{edge['src']} -[{edge['type']}]-> {edge['dst']}  @{edge['path']}:{edge['line']}"
        )
    snippet_lines: list[str] = []
    for nid, lines in sorted(result.snippets.items()):
        snippet_lines.append(f"--- {nid}")
        for line in lines:
            snippet_lines.append(line)

    raw_context = "\n".join(context_lines + snippet_lines)
    if policy.redact:
        raw_context, n1 = redact_text(raw_context)
        question, n2 = redact_text(question)
        redactions = n1 + n2

    system = (
        "You are answering a question about a code knowledge graph. "
        "Use only the supplied graph context. Cite path:line for every claim. "
        "If the context does not contain the answer, say so."
    )
    user = f"Question: {question}\n\nGraph context:\n{raw_context}"

    prompt_tokens = _estimate_tokens(system) + _estimate_tokens(user)
    notes: list[str] = []
    if prompt_tokens > policy.max_prompt_tokens:
        # Trim deterministically from the end rather than sending over the ceiling.
        budget_chars = int(policy.max_prompt_tokens * CHARS_PER_TOKEN) - len(system) - len(question) - 64
        if budget_chars < 0:
            raise EgressRefused(
                f"refused: prompt of ~{prompt_tokens} tokens exceeds ceiling "
                f"{policy.max_prompt_tokens} and cannot be trimmed."
            )
        raw_context = raw_context[:budget_chars]
        user = f"Question: {question}\n\nGraph context:\n{raw_context}"
        prompt_tokens = _estimate_tokens(system) + _estimate_tokens(user)
        notes.append(f"context trimmed to fit {policy.max_prompt_tokens}-token ceiling")

    # Conservative flat estimate; real pricing varies per model. Used only to
    # enforce the local ceiling, never reported as an invoice.
    est_cost = round((prompt_tokens + policy.max_completion_tokens) / 1_000_000 * 5.0, 6)
    if est_cost > policy.max_spend_usd:
        raise EgressRefused(
            f"refused: estimated ${est_cost} exceeds spend ceiling ${policy.max_spend_usd}"
        )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}" if api_key else "Bearer <MISSING>",
        "HTTP-Referer": "https://github.com/oruwe/codebase-cartographer",
        "X-Title": "orgono",
    }
    body = {
        "model": policy.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": policy.max_completion_tokens,
        "temperature": 0,
    }
    plan = EgressPlan(
        url=f"{policy.base_url.rstrip('/')}/chat/completions",
        model=policy.model,
        headers=headers,
        body=body,
        estimated_prompt_tokens=prompt_tokens,
        estimated_cost_usd=est_cost,
        redactions=redactions,
        notes=notes,
    )
    log.info(
        "egress.plan_built",
        model=policy.model,
        prompt_tokens=prompt_tokens,
        redactions=redactions,
        nodes=len(result.nodes),
        edges=len(result.edges),
    )
    return plan


def redacted_plan_for_display(plan: EgressPlan) -> dict:
    """The plan with the API key itself masked, for printing."""
    shown = plan.to_dict()
    shown["headers"] = dict(shown["headers"])
    shown["headers"]["Authorization"] = "Bearer [REDACTED]"
    return redact_obj(shown)


def execute(
    plan: EgressPlan,
    policy: EgressPolicy,
    api_key: str | None,
    log: Logger | None = None,
) -> dict:
    """Actually send the request. Refuses unless egress is explicitly enabled."""
    log = log or NULL_LOGGER
    if not policy.enabled:
        raise EgressRefused(
            "refused: egress is disabled. Re-run with --enable-egress "
            "(or ORGONO_EGRESS_ENABLED=1) once you have inspected the dry-run payload."
        )
    if policy.dry_run:
        raise EgressRefused(
            "refused: dry-run is still on. Pass --no-dry-run to actually send."
        )
    if not api_key:
        raise EgressRefused(
            "refused: no API key. Run `orgono auth login` or set ORGONO_OPENROUTER_API_KEY."
        )
    try:
        import requests  # imported lazily: the local pipeline has no HTTP dependency
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise EgressRefused(
            "refused: the 'egress' extra is not installed (pip install 'orgono[egress]')"
        ) from exc

    log.info("egress.send", url=plan.url, model=plan.model,
             prompt_tokens=plan.estimated_prompt_tokens)
    response = requests.post(
        plan.url, headers=plan.headers, json=plan.body, timeout=policy.timeout_seconds
    )
    response.raise_for_status()
    data = response.json()
    log.info("egress.response", status=response.status_code,
             usage=data.get("usage", {}))
    return data

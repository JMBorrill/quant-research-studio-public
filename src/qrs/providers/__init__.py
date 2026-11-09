"""Model providers (Phase 4B): the narrow interface through which `qrs extract --record` asks a
model for structured answers, and the one strict parser of every answer.

An adapter only transports: `check(endpoint, model)` before a recording's first call, then
`complete(request, endpoint) -> Reply` for each request. It never parses an answer, retries, or
stores anything. Adapters are named in a registry and imported only when a recording
calls one, so replay, strategy runs, experiments and exports never import a provider module."""

import importlib
import json
import math
from dataclasses import dataclass
from typing import Literal

from qrs.contracts import OutputField, ProviderRequest

# Tokens allowed per request beyond its text, for the provider's framing of messages and schema.
FRAMING_TOKENS = 1024


@dataclass(frozen=True)
class Reply:
    """What a provider returned for one request."""

    raw: bytes  # the exact response body
    text: str | None  # the answer text, if the provider gave one
    response_id: str | None
    models: list[str]  # every model identifier the provider reported
    finish: Literal["end", "truncated", "refused"]
    stop_reason: str  # as the provider gave it, with a refusal's category
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Provider:
    module: str
    hosted: bool  # whether requests leave this machine
    options: dict[str, type | tuple[str, ...]]  # the decoding options it accepts: a type, or the allowed values
    max_output_tokens: int
    # The option setting its context window, if it cuts a prompt longer than that without saying so.
    context_option: str | None = None


PROVIDERS = {
    "fixture": Provider("qrs.providers.fixture", hosted=False, options={}, max_output_tokens=100_000),
    "ollama": Provider(
        "qrs.providers.ollama", hosted=False, options={"temperature": float, "seed": int, "num_ctx": int}, max_output_tokens=32_768, context_option="num_ctx"
    ),
    # 16,000 output tokens, so that requests need no streaming.
    "anthropic": Provider(
        "qrs.providers.anthropic_messages", hosted=True, options={"effort": ("low", "medium", "high", "xhigh", "max")}, max_output_tokens=16_000
    ),
}


class ProviderError(RuntimeError):
    """A provider could not be asked, or did not answer: nothing was stored."""


def adapter(name: str):
    """The adapter module for a provider, imported now: only a recording calls this."""
    return importlib.import_module(PROVIDERS[name].module)


def answer_schema(fields: list[OutputField]) -> dict:
    """The JSON schema sent with every request: an object with exactly the declared numbers. It
    carries no numeric bounds, which hosted structured outputs do not accept; each field's
    description states them, and parse() enforces them."""
    return {
        "type": "object",
        "properties": {f.name: {"type": "number", "description": f"{f.description} In {f.unit}, from {f.minimum!r} to {f.maximum!r}."} for f in fields},
        "required": [f.name for f in fields],
        "additionalProperties": False,
    }


def parse(reply_text: str | None, finish: str, fields: list[OutputField]) -> tuple[dict[str, float] | None, str | None]:
    """An answer's values, or why it has none: truncated, refused, no_answer, not_json, wrong_fields,
    not_a_number or out_of_bounds. Valid only after a normal finish,
    as exactly one JSON object (no other text, no repeated key) holding exactly the declared fields,
    each a finite number within its inclusive bounds."""
    if finish in ("truncated", "refused"):
        return None, finish
    if finish != "end":
        raise ValueError(f"Unknown finish {finish!r}.")
    if reply_text is None:
        return None, "no_answer"
    try:
        answer = json.loads(reply_text, object_pairs_hook=_unique, parse_constant=_not_a_constant)
    except (ValueError, RecursionError):  # RecursionError: nesting deeper than the parser can follow
        return None, "not_json"
    if not isinstance(answer, dict):
        return None, "not_json"
    if set(answer) != {f.name for f in fields}:
        return None, "wrong_fields"
    values = {}
    for f in fields:
        value = answer[f.name]
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None, "not_a_number"
        try:
            number = float(value)
        except OverflowError:  # an integer too large for a float
            return None, "not_a_number"
        if not math.isfinite(number):
            return None, "not_a_number"
        if not f.minimum <= number <= f.maximum:
            return None, "out_of_bounds"
        values[f.name] = number
    return values, None


def _unique(pairs: list[tuple[str, object]]) -> dict:
    keys = [k for k, _ in pairs]
    if len(set(keys)) < len(keys):
        raise ValueError("repeated key")
    return dict(pairs)


def _not_a_constant(name: str):
    raise ValueError(f"{name} is not JSON")


def token_bound(request: ProviderRequest) -> tuple[int, int]:
    """Upper bounds on a request's input and output tokens: every UTF-8 byte of what is sent as one
    token, plus framing, and the output-token limit (which includes any thinking)."""
    sent = request.instructions + request.prompt + json.dumps(request.answer_schema)
    return len(sent.encode()) + FRAMING_TOKENS, request.max_output_tokens


def cost(input_tokens: int, output_tokens: int, input_price: float, output_price: float) -> float:
    """At prices per million tokens."""
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000

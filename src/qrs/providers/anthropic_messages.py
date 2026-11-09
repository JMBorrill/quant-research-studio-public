"""Anthropic (Phase 4B): the hosted adapter, the Messages API through Anthropic's official Python SDK.

The SDK is the optional extra `qrs[hosted]`, imported only here, and only when a recording calls
this adapter, so replay, runs, experiments and exports never need it. Each request:
- sends the instructions as the system text and the rendered prompt as the one user message;
- constrains the answer with structured outputs to the answer schema, which has no numeric bounds;
- sends no tools, no sampling settings (which the API rejects) and no fallback to another model, so
  every response comes from the requested model or is a refusal;
- has SDK retries off, so a transport error stops the recording, which then resumes from the store.

Credentials come from the SDK's own sources, such as ANTHROPIC_API_KEY; nothing in QRS reads them."""

from qrs.contracts import ProviderRequest
from qrs.providers import ProviderError, Reply

TIMEOUT_SECONDS = 600
FINISHES = {"end_turn": "end", "refusal": "refused"}  # anything else stopped before a complete answer


def sdk():
    try:
        import anthropic
    except ImportError:
        raise ProviderError("Recording through Anthropic needs its SDK: install the optional extra, qrs[hosted].") from None
    return anthropic


def check(endpoint: str | None, model: str) -> None:
    """Before a recording's first call: the SDK is installed."""
    sdk()


def complete(request: ProviderRequest, endpoint: str | None) -> Reply:
    anthropic = sdk()
    output_config = {"format": {"type": "json_schema", "schema": request.answer_schema}}
    if "effort" in request.options:
        output_config["effort"] = request.options["effort"]
    try:
        # No telemetry: prompts hold document text, which an exporter configured for the SDK could copy.
        with anthropic.Anthropic(max_retries=0, timeout=TIMEOUT_SECONDS, open_telemetry=False) as client:
            response = client.messages.with_raw_response.create(
                model=request.model,
                max_tokens=request.max_output_tokens,
                system=request.instructions,
                messages=[{"role": "user", "content": request.prompt}],
                output_config=output_config,
            )
            raw = response.read()
            message = response.parse()
    except anthropic.AnthropicError as e:
        raise ProviderError(f"Anthropic did not answer: {type(e).__name__}: {e}") from None
    stop_reason = str(message.stop_reason)
    category = getattr(message.stop_details, "category", None) if message.stop_details else None
    usage = message.usage
    return Reply(
        raw=raw,
        text="".join(block.text for block in message.content if block.type == "text") or None,
        response_id=message.id,
        models=[message.model],
        finish=FINISHES.get(stop_reason, "truncated"),
        stop_reason=f"{stop_reason} ({category})" if category else stop_reason,
        input_tokens=usage.input_tokens + (usage.cache_creation_input_tokens or 0) + (usage.cache_read_input_tokens or 0),
        output_tokens=usage.output_tokens,
    )

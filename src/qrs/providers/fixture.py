"""The fixture provider (Phase 4B): deterministic answers from a JSON file, offline. Templates and
tests record with it, and it stands in for a real provider wherever the interface is exercised.

The endpoint is the answers file. It maps the SHA-256 of a request's rendered prompt to the answer
text a model would return, or to {"text": ..., "finish": "truncated" | "refused"} for an answer that
did not finish normally. A prompt with no entry gets no answer. Like any provider, the fixture sees
only the request, never the document it came from, and stops at the output-token limit: a longer
answer is truncated there."""

import json
from pathlib import Path

from qrs.contracts import ProviderRequest
from qrs.hashing import sha256_hex
from qrs.providers import ProviderError, Reply

BYTES_PER_TOKEN = 4  # the fixture's own token count: a deterministic stand-in for a tokenizer


def check(endpoint: str | None, model: str) -> None:
    """Before a recording's first call: the answers file must be readable."""
    if endpoint is None or not Path(endpoint).is_file():
        raise ProviderError(f"The fixture answers file {endpoint} cannot be read.")


def complete(request: ProviderRequest, endpoint: str | None) -> Reply:
    if endpoint is None:
        raise ProviderError("The fixture provider needs its answers file as the endpoint.")
    try:
        content = Path(endpoint).read_bytes()
        answers = json.loads(content)
    except (OSError, ValueError) as e:
        raise ProviderError(f"The fixture answers file {endpoint} cannot be read: {e}") from None
    key = sha256_hex(request.prompt.encode())
    text, finish = answer(answers, key, endpoint)
    tokens = -(-len((text or "").encode()) // BYTES_PER_TOKEN)
    if tokens > request.max_output_tokens:
        text = (text or "").encode()[: request.max_output_tokens * BYTES_PER_TOKEN].decode(errors="ignore")
        tokens, finish = request.max_output_tokens, "truncated"
    raw = json.dumps({"prompt_sha256": key, "text": text, "finish": finish}, sort_keys=True).encode()
    return Reply(
        raw=raw,
        text=text,
        response_id=f"fixture-{key[:16]}",
        models=[f"fixture:{sha256_hex(content)}"],
        finish=finish,
        stop_reason=finish,
        input_tokens=-(-len((request.instructions + request.prompt).encode()) // BYTES_PER_TOKEN),
        output_tokens=tokens,
    )


def answer(answers: object, key: str, endpoint: str) -> tuple[str | None, str]:
    """The answer text and how it finished, for a prompt's key: none, normally, if the file has no
    entry for it."""
    if not isinstance(answers, dict):
        raise ProviderError(f"The fixture answers file {endpoint} must map prompt hashes to answers.")
    entry = answers.get(key)
    if entry is None or isinstance(entry, str):
        return entry, "end"
    if isinstance(entry, dict) and set(entry) == {"text", "finish"} and entry["finish"] in ("truncated", "refused") and isinstance(entry["text"], str | None):
        return entry["text"], entry["finish"]
    raise ProviderError(f"The fixture answer for {key[:12]} in {endpoint} must be text, or text with a finish of truncated or refused.")

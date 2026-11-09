"""Ollama (Phase 4B): local inference through Ollama's native chat API, `POST /api/chat`.

Text must not leave the machine, so the adapter:
- resolves the endpoint's host and refuses it unless every address is a loopback address;
- connects to the address it checked, with `http.client`, which never consults proxy settings, so
  neither `http_proxy` nor DNS rebinding can send a request elsewhere;
- asks only a model Ollama lists locally with a digest, and refuses one Ollama lists as remote,
  which it would forward to a hosted service.

It uses only the standard library, so it adds no dependency."""

import http.client
import ipaddress
import json
import socket
import urllib.parse

from qrs.contracts import ProviderRequest
from qrs.providers import ProviderError, Reply

DEFAULT_PORT = 11434
TIMEOUT_SECONDS = 600


def loopback(endpoint: str | None) -> tuple[str, int]:
    """The loopback address and port to connect to, or ProviderError if the endpoint is not an
    http:// address whose host resolves only to loopback addresses."""
    refused = ProviderError(f"The Ollama endpoint must be a base http:// address, such as http://127.0.0.1:{DEFAULT_PORT}, not {endpoint!r}.")
    try:
        url = urllib.parse.urlsplit(endpoint or "")
        port = DEFAULT_PORT if url.port is None else url.port
    except ValueError:  # such as a port out of range, or an unclosed IPv6 bracket
        raise refused from None
    if url.scheme != "http" or not url.hostname or url.username is not None or not 0 < port < 65536 or url.path not in ("", "/") or url.query or url.fragment:
        raise refused
    try:
        addresses = sorted({info[4][0] for info in socket.getaddrinfo(url.hostname, port, type=socket.SOCK_STREAM)})
    except OSError as e:
        raise ProviderError(f"The Ollama endpoint's host {url.hostname!r} cannot be resolved: {e}.") from None
    if not all(ipaddress.ip_address(a.split("%")[0]).is_loopback for a in addresses):
        raise ProviderError(f"The Ollama endpoint's host {url.hostname!r} resolves to {addresses}: only loopback addresses keep text on this machine.")
    return addresses[0], port


def call(endpoint: str | None, method: str, path: str, body: dict | None = None) -> bytes:
    address, port = loopback(endpoint)
    connection = http.client.HTTPConnection(address, port, timeout=TIMEOUT_SECONDS)
    try:
        connection.request(method, path, body=None if body is None else json.dumps(body), headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        content = response.read()
    except (OSError, http.client.HTTPException) as e:  # HTTPException: a server that is not Ollama, or one that died mid-answer
        raise ProviderError(f"Ollama at {endpoint} did not answer: {e}.") from None
    finally:
        connection.close()
    if response.status != 200:
        raise ProviderError(f"Ollama at {endpoint} answered {response.status}: {content[:200].decode(errors='replace')}")
    return content


def digest(endpoint: str | None, model: str) -> str:
    """The digest of the model Ollama lists locally, refusing one it lists as remote."""
    try:
        listed = json.loads(call(endpoint, "GET", "/api/tags"))["models"]
    except (ValueError, KeyError, TypeError):
        raise ProviderError(f"Ollama at {endpoint} did not list its models.") from None
    names = {model, f"{model}:latest"}
    for entry in listed:
        if entry.get("name") in names or entry.get("model") in names:
            if entry.get("remote_host") or entry.get("remote_model"):
                raise ProviderError(f"Ollama runs {model} remotely, at {entry.get('remote_host')}: only local models keep text on this machine.")
            if not entry.get("digest"):
                raise ProviderError(f"Ollama lists {model} without a digest.")
            return entry["digest"]
    raise ProviderError(f"Ollama at {endpoint} has no local model {model}; pull it first.")


def check(endpoint: str | None, model: str) -> None:
    """Before a recording's first call: a loopback endpoint serving the model locally."""
    digest(endpoint, model)


def context_length(endpoint: str | None, model: str) -> int:
    """The context length the model was trained for, as Ollama reports it."""
    try:
        info = json.loads(call(endpoint, "POST", "/api/show", {"model": model}))["model_info"]
        return int(info[f"{info['general.architecture']}.context_length"])
    except (ValueError, KeyError, TypeError):
        raise ProviderError(f"Ollama at {endpoint} did not report the context length of {model}.") from None


def complete(request: ProviderRequest, endpoint: str | None) -> Reply:
    model_digest = digest(endpoint, request.model)  # again, so a model re-pulled mid-recording is seen
    # Ollama lowers a num_ctx above the model's context length to it, again without saying so.
    trained, window = context_length(endpoint, request.model), request.options["num_ctx"]
    if window > trained:
        raise ProviderError(
            f"{request.model} has a context length of {trained} tokens, which Ollama would use instead of num_ctx {window}, so a request could be cut."
        )
    body = {
        "model": request.model,
        "messages": [{"role": "system", "content": request.instructions}, {"role": "user", "content": request.prompt}],
        "format": request.answer_schema,
        "stream": False,
        "shift": False,  # so a current Ollama refuses, rather than cuts, a prompt longer than the window
        "options": {**request.options, "num_predict": request.max_output_tokens},
    }
    raw = call(endpoint, "POST", "/api/chat", body)
    try:
        answer = json.loads(raw)
        text = answer["message"]["content"]
    except (ValueError, KeyError, TypeError):
        raise ProviderError(f"Ollama at {endpoint} returned no chat message.") from None
    stop_reason = str(answer.get("done_reason"))
    return Reply(
        raw=raw,
        text=text,
        response_id=answer.get("created_at"),
        models=[f"{answer.get('model', request.model)}@{model_digest}"],
        finish="end" if stop_reason == "stop" else "truncated",
        stop_reason=stop_reason,
        input_tokens=int(answer.get("prompt_eval_count", 0)),
        output_tokens=int(answer.get("eval_count", 0)),
    )

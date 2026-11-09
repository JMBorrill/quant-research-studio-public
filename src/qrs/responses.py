"""The response store (Phase 4B): every provider response, kept once and never changed.

A record is <home>/responses/<request SHA-256>.json. The key is the hash of the canonical request,
so asking the same thing again finds the stored answer instead of calling the provider; changing
the extraction's capture label is the only way to ask again. Records are written once, by linking
a complete temporary file into place, and made read-only: a different record for a stored request
is refused, so a stored response can never be replaced."""

import os
import tempfile
from pathlib import Path

from pydantic import ValidationError

from qrs.contracts import ProviderRequest, ResponseRecord
from qrs.hashing import canonical_json, sha256_hex


class ResponseError(RuntimeError):
    pass


def request_sha256(request: ProviderRequest) -> str:
    return sha256_hex(canonical_json(request.model_dump(mode="json")))


def record_path(home: Path, key: str) -> Path:
    return home / "responses" / f"{key}.json"


def load(home: Path, key: str) -> ResponseRecord | None:
    """The stored record for a request, or None if there is none. A record that does not parse, or
    that answers another request, is refused."""
    path = record_path(home, key)
    if not path.exists():
        return None
    try:
        record = ResponseRecord.model_validate_json(path.read_bytes())
    except ValidationError as e:
        raise ResponseError(f"The stored response {path} is corrupted: {e.error_count()} problem(s).") from None
    if request_sha256(record.request) != key:
        raise ResponseError(f"The stored response {path} answers a different request.")
    return record


def save(home: Path, record: ResponseRecord) -> str:
    """Store a record once; return its key. Storing an identical record again is a no-op."""
    key = request_sha256(record.request)
    path = record_path(home, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = canonical_json(record.model_dump(mode="json"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())  # complete on disk before it is linked into place, never to be replaced
        os.chmod(tmp, 0o444)
        try:
            os.link(tmp, path)  # fails if a record exists: never replaces one
        except FileExistsError:
            if path.read_bytes() != content:
                raise ResponseError(f"A different response is already stored for request {key}; stored responses are never replaced.") from None
    finally:
        os.unlink(tmp)
    return key

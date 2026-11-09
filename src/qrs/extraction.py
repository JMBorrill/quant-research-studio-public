"""Extraction (Phase 4B): documents to an immutable feature dataset, through stored model responses.

`qrs extract EXTRACTION.json` replays: it builds the dataset from stored responses only, never
importing or constructing a provider, and fails, naming the documents, if any response is missing.
`--record` first asks the provider for the missing responses, within the extraction's budget,
storing each; then it replays. Strategies read the result as observations, so no backtest,
experiment or export ever calls a provider ([stage-4b-plan.md](../../docs/stage-4b-plan.md)
decisions 1, 3–7)."""

import base64
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from qrs import datasets, providers, responses
from qrs.contracts import PLACEHOLDER, Derivation, DocumentsManifest, Extraction, ObservationsManifest, ProviderRequest, ResponseRecord
from qrs.hashing import canonical_json, sha256_hex

MAX_LISTED = 10


class ExtractionError(RuntimeError):
    pass


def now() -> datetime:
    return datetime.now(UTC)


def extract(home: Path, spec: Extraction, record: bool = False, report: Callable[[str], None] = print) -> str:
    """Build the extraction's feature dataset and return its ID; with `record`, first ask the
    provider for any response not yet stored."""
    documents_manifest, documents = datasets.load_documents(home, spec.documents)
    provider = check(spec, documents_manifest)
    rows, duplicates = select(documents, spec.dedupe)
    requests = [request_for(spec, title, text) for title, text in zip(rows["title"], rows["text"], strict=True)]
    check_context(spec, provider, requests)
    keys = [responses.request_sha256(r) for r in requests]
    if record:
        labels = {key: label(d, r) for key, d, r in zip(keys, rows["document"], rows["revision"], strict=True)}
        record_missing(home, spec, provider, dict(zip(keys, requests, strict=True)), labels, report)
    records = [responses.load(home, key) for key in keys]
    missing = [label(d, r) for d, r, rec in zip(rows["document"], rows["revision"], records, strict=True) if rec is None]
    if missing:
        listed = ", ".join(missing[:MAX_LISTED]) + (f" and {len(missing) - MAX_LISTED} more" if len(missing) > MAX_LISTED else "")
        raise ExtractionError(
            f"{len(missing)} document version(s) have no stored response, so the dataset cannot be built: {listed}. Record them with --record."
        )
    return publish(home, spec, documents_manifest, provider, rows, keys, records, duplicates, first_needed(spec, documents))


def check_context(spec: Extraction, provider: providers.Provider, requests: list[ProviderRequest]) -> None:
    """A provider that cuts a prompt longer than its context window without saying so (Ollama) must
    be given a window that holds every request whole: its input bound and its output limit. Checked
    in replay too, so that no dataset is built from answers to prompts that may have been cut."""
    if provider.context_option is None:
        return
    needed = max((sum(providers.token_bound(request)) for request in requests), default=0)
    declared = spec.options.get(provider.context_option)
    if declared is None or declared < needed:
        raise ExtractionError(
            f"{spec.provider} cuts a prompt longer than its context window without saying so, so options.{provider.context_option} must hold "
            f"every request whole: at least {needed} tokens here (the largest request's bound, output included), not "
            f"{'the default' if declared is None else declared}. Nothing was asked."
        )


def label(document: str, revision: str) -> str:
    return f"{document} ({revision or 'no revision'})"


def first_needed(spec: Extraction, documents: pd.DataFrame) -> dict[str, pd.Timestamp]:
    """When each request was first needed: the earliest publication of the document versions that
    render it, copies that deduplication drops included. Its stored response can predate no other."""
    keys = [responses.request_sha256(request_for(spec, title, text)) for title, text in zip(documents["title"], documents["text"], strict=True)]
    return pd.Series(documents["published"].to_numpy()).groupby(keys).min().to_dict()


def check(spec: Extraction, documents: DocumentsManifest) -> providers.Provider:
    """Everything that can be refused without an adapter, so replay refuses it too, before any call."""
    provider = providers.PROVIDERS[spec.provider]
    for name, value in spec.options.items():
        expected = provider.options.get(name)
        if expected is None:
            raise ExtractionError(f"{spec.provider} accepts no option {name!r}; it accepts {sorted(provider.options) or 'none'}.")
        if isinstance(expected, tuple) and value not in expected:
            raise ExtractionError(f"Option {name!r} must be one of {', '.join(expected)}, not {value!r}.")
        if not isinstance(expected, tuple) and not of_type(value, expected):
            raise ExtractionError(f"Option {name!r} must be a {expected.__name__}, not {value!r}.")
    if spec.max_output_tokens > provider.max_output_tokens:
        raise ExtractionError(f"max_output_tokens {spec.max_output_tokens} is over {spec.provider}'s limit of {provider.max_output_tokens}.")
    if provider.hosted and spec.endpoint is not None:
        raise ExtractionError(f"{spec.provider} is hosted: its endpoint is fixed, and an extraction cannot name one.")
    if not provider.hosted and spec.endpoint is None:
        raise ExtractionError(f"{spec.provider} needs an endpoint.")
    if provider.hosted and not documents.hosted_processing:
        raise ExtractionError(f"The documents {documents.name!r} may not be sent to a hosted provider (hosted_processing is false).")
    return provider


def of_type(value: object, expected: type) -> bool:
    """Whether an option's value is of its type: a boolean is never a number, and an integer is a
    float."""
    if isinstance(value, bool) or expected is bool:
        return isinstance(value, bool) and expected is bool
    return isinstance(value, int | float) if expected is float else isinstance(value, expected)


def select(documents: pd.DataFrame, dedupe: bool) -> tuple[pd.DataFrame, int]:
    """The document versions to extract, in publication order, and how many were dropped as copies
    of text (whitespace aside) that another document published first. Once a document has a version
    kept, every later version of it is kept, even one that reverts to its own earlier text or turns
    into another's: dropping it would leave the version it replaces counting."""
    if not dedupe:
        return documents, 0
    first_document: dict[str, str] = {}
    kept_documents: set[str] = set()
    keep = []
    for document, text in zip(documents["document"], documents["text"], strict=True):
        if first_document.setdefault(" ".join(text.split()), document) == document:
            kept_documents.add(document)
        keep.append(document in kept_documents)
    kept = pd.Series(keep, index=documents.index)
    return documents[kept].reset_index(drop=True), int((~kept).sum())


def render(prompt: str, title: str, text: str) -> str:
    """The prompt with the document's title and text in place, in one pass, so text that looks like
    a placeholder is never substituted again."""
    return PLACEHOLDER.sub(lambda m: {"title": title, "text": text}[m[1]], prompt)


def request_for(spec: Extraction, title: str, text: str) -> ProviderRequest:
    return ProviderRequest(
        provider=spec.provider,
        model=spec.model,
        instructions=spec.instructions,
        prompt=render(spec.prompt, title, text),
        answer_schema=providers.answer_schema(spec.fields),
        max_output_tokens=spec.max_output_tokens,
        options=spec.options,
        capture=spec.capture,
    )


def record_missing(
    home: Path, spec: Extraction, provider: providers.Provider, requests: dict[str, ProviderRequest], labels: dict[str, str], report: Callable[[str], None]
) -> None:
    """Ask the provider for every request with no stored response, one at a time, storing each. The
    budget bounds the whole extraction: its stored responses, at their recorded cost, and the upper
    bound of those still missing must fit it before the first call. A provider error, or a response
    reporting more tokens than its bound, stops the recording."""
    existing = {key: responses.load(home, key) for key in requests}
    missing = {key: request for key, request in requests.items() if existing[key] is None}
    if not missing:
        report("Every response is stored: nothing to record.")
        return
    budget = spec.budget
    bounds = {key: providers.token_bound(request) for key, request in missing.items()}
    spent = sum(record.cost for record in existing.values() if record is not None)
    bound_cost = spent + sum(providers.cost(i, o, budget.input_price, budget.output_price) for i, o in bounds.values())
    if len(requests) > budget.max_requests:
        raise ExtractionError(f"The extraction needs {len(requests)} requests, over the budget's {budget.max_requests}. Nothing was asked.")
    if bound_cost > budget.max_cost:
        raise ExtractionError(
            f"The extraction could cost up to {bound_cost:.6g} ({spent:.6g} already spent), over the budget's {budget.max_cost:g}. Nothing was asked."
        )
    adapter = providers.adapter(spec.provider)
    try:
        adapter.check(spec.endpoint, spec.model)
        for n, (key, request) in enumerate(missing.items(), start=1):
            started = time.monotonic()
            reply = adapter.complete(request, spec.endpoint)
            latency = time.monotonic() - started
            cost = providers.cost(reply.input_tokens, reply.output_tokens, budget.input_price, budget.output_price)
            responses.save(home, stored(request, reply, cost, latency))
            spent += cost
            report(f"recorded {n}/{len(missing)} {labels[key]} {key[:12]}: {reply.finish}, {reply.input_tokens}+{reply.output_tokens} tokens")
            if reply.input_tokens > bounds[key][0] or reply.output_tokens > bounds[key][1]:
                raise ExtractionError(f"Response {key[:12]} reported more tokens than its bound {bounds[key]}, so the recording stopped.")
    except providers.ProviderError as e:
        raise ExtractionError(f"Recording stopped: {e}. Stored responses are kept; once the cause is fixed, run it again to continue.") from None
    report(f"recorded {len(missing)} responses; the extraction's responses have cost {spent:.6g}")


def stored(request: ProviderRequest, reply: providers.Reply, cost: float, latency: float) -> ResponseRecord:
    return ResponseRecord(
        request=request,
        raw_base64=base64.b64encode(reply.raw).decode(),
        text=reply.text,
        response_id=reply.response_id,
        models=reply.models,
        finish=reply.finish,
        stop_reason=reply.stop_reason,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        cost=cost,
        captured_at=now(),
        latency_seconds=latency,
    )


def publish(
    home: Path,
    spec: Extraction,
    documents: DocumentsManifest,
    provider: providers.Provider,
    rows: pd.DataFrame,
    keys: list[str],
    records: list[ResponseRecord],
    duplicates: int,
    first_needed: dict[str, pd.Timestamp],
) -> str:
    """The feature dataset: one row per document version, usable from its publication plus the
    larger of the declared and measured latency, with the parsed values, or none and a reason."""
    parsed = [providers.parse(r.text, r.finish, spec.fields) for r in records]
    usable = usable_times(rows, spec.latency_seconds, records)
    captured = pd.to_datetime([r.captured_at for r in records], utc=True)
    early = [label(d, r) for d, r, key, c in zip(rows["document"], rows["revision"], keys, captured, strict=True) if c < first_needed[key]]
    if early:
        raise ExtractionError(
            f"Stored responses for {', '.join(early[:MAX_LISTED])} were captured before their documents were published: the store was altered."
        )
    retrospective = captured > usable
    frame = pd.DataFrame(
        {
            "event": rows["event"].to_numpy(),
            "published": usable,
            "revision": rows["revision"].to_numpy(),
            "estimated": retrospective | rows["estimated"].to_numpy(),
            "document": rows["document"].to_numpy(),
            **{f.name: [values[f.name] if values else float("nan") for values, _ in parsed] for f in spec.fields},
        }
    )
    order = frame.sort_values(["event", "published"], kind="stable").index
    lines = [
        {
            "document": d,
            "revision": r,
            "published": p.isoformat(),  # the document's, before its latency
            "url": u,
            "request_sha256": key,
            "status": reason or "valid",
            "record": record.model_dump(mode="json"),
        }
        for d, r, p, u, key, record, (_, reason) in zip(rows["document"], rows["revision"], rows["published"], rows["url"], keys, records, parsed, strict=True)
    ]
    files = {
        "extraction.json": canonical_json(spec.model_dump(mode="json")),
        "responses.jsonl": b"".join(canonical_json(lines[i]) + b"\n" for i in order),
        # The documents' source, file hash and column mapping, without their text: its hash is the
        # documents dataset ID.
        "documents.json": (datasets.directory(home, spec.documents) / "manifest.json").read_bytes(),
    }
    distinct = {key: record for key, record in zip(keys, records, strict=True)}.values()
    derivation = Derivation(
        documents=spec.documents,
        extraction_sha256=sha256_hex(files["extraction.json"]),
        responses_sha256=sha256_hex(files["responses.jsonl"]),
        provider=spec.provider,
        model=spec.model,
        reported_models=sorted({m for r in distinct for m in r.models}),
        hosted=provider.hosted,
        latency_seconds=spec.latency_seconds,
        capture=spec.capture,
        requests=len(distinct),
        retrospective_rows=int(retrospective.sum()),
        duplicates=duplicates,
        invalid_rows=dict(sorted(Counter(reason for _, reason in parsed if reason).items())),
        input_tokens=sum(r.input_tokens for r in distinct),
        output_tokens=sum(r.output_tokens for r in distinct),
        cost=sum(r.cost for r in distinct),
    )
    frame = frame.loc[order].reset_index(drop=True)
    estimated = int(frame["estimated"].sum())
    return datasets.save_features(
        home,
        frame,
        files,
        lambda data_sha256: ObservationsManifest(
            name=spec.name,
            values={f.name: f.unit for f in spec.fields},
            evidence_class=datasets.weakest([documents.evidence_class, *(["exploratory"] if retrospective.any() else [])]),
            source=f"{documents.name}, extracted by {spec.provider} {spec.model}",
            redistributable=spec.redistributable and documents.redistributable,
            estimates=estimates(spec, documents, derivation.retrospective_rows) if estimated else None,
            rows=len(frame),
            estimated_rows=estimated,
            data_sha256=data_sha256,
            derivation=derivation,
        ),
    )


def usable_times(rows: pd.DataFrame, declared: float, records: list[ResponseRecord]) -> pd.DatetimeIndex:
    """When each row can be used: its document's publication plus the larger of the declared and
    the measured latency, taking for each version the largest latency of that document's versions
    so far. A correction then always becomes usable after the version it corrects, however slowly
    that version was answered."""
    latency = pd.Series([max(declared, r.latency_seconds) for r in records], index=rows.index)
    running = latency.groupby(rows["document"].to_numpy()).cummax()  # rows are in publication order
    return pd.DatetimeIndex(rows["published"]) + pd.to_timedelta(running.to_numpy(), unit="s")


def estimates(spec: Extraction, documents: DocumentsManifest, retrospective: int) -> str:
    """How the estimated rows' publication times were made."""
    said = []
    if retrospective:
        said.append(
            f"{retrospective} row(s) are usable from their document's publication plus the larger of the declared "
            f"({spec.latency_seconds:g} s) and measured latency, but their responses were captured later"
        )
    if documents.estimates:
        said.append(f"the documents' own timestamps were estimated: {documents.estimates}")
    return "; ".join(said) + "."

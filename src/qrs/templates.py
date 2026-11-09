"""Template registry: versioned strategy JSON files in qrs/templates/, each validated as a Template
and paired with a fixture CSV of the same name. A template that reads features (Phase 4B) also has
documents, the fixture provider's answers and the responses recorded from them. Instantiating a
template copies its graph into a new, independently editable strategy and records the template ID,
version and hash; later template changes never alter saved strategies."""

import re
from pathlib import Path

from qrs import datasets, extraction, importer, registry, responses
from qrs.contracts import ResponseRecord, StrategyDocument, Template, TemplateRef
from qrs.hashing import sha256_hex

TEMPLATE_DIR = Path(__file__).parent / "templates"
LISTED = {"id", "version", "title", "description", "required_bar", "conditions"}
TEMPLATE_ID = re.compile(r"^[a-z][a-z0-9_]*$")  # so a fixture's own files, such as news.answers.json, are not templates


def template_files() -> dict[str, Path]:
    return {p.stem: p for p in sorted(TEMPLATE_DIR.glob("*.json")) if TEMPLATE_ID.match(p.stem)}


def path(template_id: str) -> Path:
    """The template's file. Only IDs of files in TEMPLATE_DIR are accepted, so an ID cannot name another file."""
    paths = template_files()
    if template_id not in paths:
        raise KeyError(f"Unknown template {template_id}.")
    return paths[template_id]


def load(template_id: str) -> Template:
    return Template.model_validate_json(path(template_id).read_bytes())


def list_templates() -> list[dict]:
    out = []
    for file in template_files().values():
        raw = file.read_bytes()
        template = Template.model_validate_json(raw)
        out.append({**template.model_dump(mode="json", include=LISTED), "sha256": sha256_hex(raw), "datasets": kinds(template.strategy)})
    return out


def kinds(doc: StrategyDocument) -> list[str]:
    """The kinds of dataset a strategy reads, such as bars and observations."""
    found = set()
    for node in doc.nodes:
        op = registry.find(node.operator, node.version)
        found |= {op.dataset_kind(param) for param in op.dataset_params()} if op else set()
    return sorted(found)


def instantiate(template_id: str, dataset_id: str | None = None, name: str | None = None, chosen: dict[str, str] | None = None) -> StrategyDocument:
    """A copy of the template's strategy. Each dataset parameter takes the chosen dataset of the kind
    it declares; `dataset_id` is the chosen bars, as before templates read features."""
    raw = path(template_id).read_bytes()
    template = Template.model_validate_json(raw)
    doc = template.strategy
    doc.template = TemplateRef(id=template.id, version=template.version, sha256=sha256_hex(raw))
    if name:
        doc.name = name
    by_kind = {**({datasets.BARS: dataset_id} if dataset_id else {}), **(chosen or {})}
    for node in doc.nodes:
        op = registry.find(node.operator, node.version)
        for param in op.dataset_params() if op else []:
            if op.dataset_kind(param) in by_kind:
                node.params[param] = by_kind[op.dataset_kind(param)]
    return doc


def prepare(template_id: str, data_dir: Path) -> dict[str, str]:
    """Import a template's fixture into data_dir, with its features if it reads any; the dataset
    IDs by kind, to instantiate it with."""
    template = load(template_id)
    bars = importer.import_csv(data_dir, (TEMPLATE_DIR / f"{template_id}.csv").read_bytes(), template.fixture.declaration)
    features = prepare_features(template_id, data_dir)
    return {datasets.BARS: bars, **({datasets.OBSERVATIONS: features} if features else {})}


def prepare_features(template_id: str, data_dir: Path) -> str | None:
    """For a template that reads features: import its documents, store its recorded responses and
    replay its extraction into data_dir, never calling a provider; the features' dataset ID."""
    fixture = load(template_id).fixture
    if fixture.extraction is None:
        return None
    documents = importer.import_documents(data_dir, (TEMPLATE_DIR / f"{template_id}.documents.csv").read_bytes(), fixture.documents)
    if documents != fixture.extraction.documents:
        raise ValueError(f"Template {template_id}'s documents import as {documents}, not the {fixture.extraction.documents} its extraction names.")
    for line in (TEMPLATE_DIR / f"{template_id}.responses.jsonl").read_text(encoding="utf-8").splitlines():
        responses.save(data_dir, ResponseRecord.model_validate_json(line))
    return extraction.extract(data_dir, fixture.extraction, report=lambda _: None)

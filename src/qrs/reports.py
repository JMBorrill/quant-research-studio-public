"""Reports and evidence trails (Stage 5 decision 6). A report is generated from stored artefacts,
never written by hand: a document of sections whose blocks are text, or figures, tables, values and
family results referenced by ID. Building it writes, into a new directory:

- report.json, the document;
- report.html: self-contained, its SVGs inline, with no scripts or external resources, every text
  escaped, and a content security policy that forbids any;
- evidence.json, fixed at a point in the store's history (a time and the last read then): every
  dataset, analysis and family the report rests on, with their hashes, rights and evidence classes;
  the read horizons; how many of the project's analyses ended each way; every family registered in
  the project; and the runtime identity;
- record.json, with the hashes of both files and the report's hash over them.

`verify` rebuilds both files from the store as of the same point, checking every artefact against
its record on the way, and compares them byte for byte: any changed artefact is found, and the store
growing after the build changes nothing."""

import csv
import io
import json
from html import escape
from importlib.metadata import version
from pathlib import Path

from qrs import datasets, projects, runtime
from qrs.contracts import ReportDocument
from qrs.hashing import canonical_json, sha256_file, sha256_hex
from qrs.store import NotFound, Store

TABLE_ROWS = 50  # rows of a table shown in a report; the rest are in its analysis, by hash
FILES = ("report.html", "evidence.json")


class ReportError(ValueError):
    pass


def build(store: Store, jobs, doc: ReportDocument, out_dir: Path, shareable: bool = False) -> dict:
    """Build a report into the new directory out_dir, as of now; its record. With `shareable`, refuse
    a report that would include anything not redistributable (Stage 5 P11)."""
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise ReportError(f"{out_dir} is not an empty directory; choose a new output directory.")
    as_of = projects.AsOf.now(store)
    runtime_now = {"identity": runtime.runtime_identity(), "scipy": version("scipy")}
    evidence, html = _assemble(store, jobs, doc, as_of, runtime_now)
    if shareable and not evidence["redistributable"]:
        raise ReportError(f"The report includes data whose terms forbid redistribution: {', '.join(evidence['restricted'])}.")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(doc.model_dump_json(indent=2) + "\n")
    (out_dir / "report.html").write_bytes(html.encode())
    (out_dir / "evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    outputs = {name: sha256_file(out_dir / name) for name in FILES}
    record = {"kind": "qrs/report-record", "version": 1, "outputs": outputs, "report_sha256": sha256_hex(canonical_json(outputs))}
    (out_dir / "record.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def verify(store: Store, jobs, report_dir: Path) -> list[str]:
    """Why a built report no longer matches the store, as of the point it was built; empty if it does."""
    try:
        doc = ReportDocument.model_validate_json((report_dir / "report.json").read_text())
        built = json.loads((report_dir / "evidence.json").read_text())
        record = json.loads((report_dir / "record.json").read_text())
    except (OSError, ValueError) as e:
        return [f"The report cannot be read: {e}"]
    problems = [f"{name} does not match the hash in record.json." for name in FILES if sha256_file(report_dir / name) != record["outputs"].get(name)]
    try:
        evidence, html = _assemble(store, jobs, doc, projects.AsOf(**built["as_of"]), built["runtime"])
    except (ReportError, projects.ProjectError, OSError, ValueError, KeyError, TypeError) as e:
        return problems + [f"The report cannot be rebuilt from the store: {e}"]
    if canonical_json(evidence) != canonical_json(built):
        changed = sorted(key for key in set(evidence) | set(built) if evidence.get(key) != built.get(key))
        problems.append(f"The evidence differs from the store's, in: {', '.join(changed)}.")
    if html.encode() != (report_dir / "report.html").read_bytes():  # bytes: reading text would translate \r\n
        problems.append("The report's text differs from the one the store gives.")
    return problems


def _assemble(store: Store, jobs, doc: ReportDocument, as_of: projects.AsOf, identity: dict) -> tuple[dict, str]:
    project = projects.load(store, doc.project)
    if store.get_project(doc.project)["created_at"] > as_of.time:
        raise ReportError(f"Project {doc.project} did not exist yet at {as_of.time}.")
    families = {f["id"]: projects.status(store, jobs, f["id"], as_of) for f in store.families(doc.project) if f["registered_at"] <= as_of.time}
    referenced = [b.family for s in doc.sections for b in s.blocks if b.kind == "family"]
    for family_id in referenced:
        if family_id not in families:
            raise ReportError(f"Family {family_id} is not one of the project's, as of {as_of.time}.")
    shown = [b.analysis for s in doc.sections for b in s.blocks if b.kind in ("figure", "table", "values")]
    for analysis_id in shown:  # only the project's own analyses, which its count discloses
        if _analysis_row(store, analysis_id)["project"] != doc.project:
            raise ReportError(f"Analysis {analysis_id} is not one of the project's.")
    # Every evaluation that had succeeded by then, of every family listed, is checked against its record:
    # the numbers each family shows trace to a hash. The others are shown by their state.
    evaluated = [e["analysis"]["id"] for f in families.values() for e in f["evaluations"] if e["analysis"] and e["analysis"]["status"] == "succeeded"]
    analyses = {i: _analysis(store, jobs, i, as_of) for i in dict.fromkeys(shown + evaluated)}
    dataset_ids = sorted(set(project.datasets) | {d for a in analyses.values() for d in a["datasets"]})
    manifests = datasets.list_manifests(store.home)
    dataset_info = {i: _dataset(manifests.get(i)) for i in dataset_ids}
    findings = [_finding(block, analyses, families) for section in doc.sections for block in section.blocks if block.kind != "text"]
    classes = [f["evidence_class"] for f in findings if f["evidence_class"]]
    restricted = sorted({dataset_info[d]["name"] for a in analyses.values() for d in a["datasets"] if not dataset_info[d]["redistributable"]})
    statuses = [projects.as_of_status(a, as_of) for a in store.project_analyses(doc.project) if a["created_at"] <= as_of.time]
    evidence = {
        "kind": "qrs/evidence",
        "version": 1,
        "as_of": {"time": as_of.time, "read": as_of.read},
        "project": {
            "id": doc.project,
            "document": project.model_dump(mode="json"),
            "notes": [n for n in store.notes(doc.project) if n["saved_at"] <= as_of.time],
        },
        "datasets": dataset_info,
        "horizons": projects.horizons(store, project, as_of),
        "analyses": analyses,
        "project_analyses": {s: statuses.count(s) for s in sorted(set(statuses))},
        "families": {i: _family(f, analyses) for i, f in families.items()},
        "findings": findings,
        "evidence_class": datasets.weakest(classes) if classes else None,  # the report's: the weakest of its findings
        "redistributable": not restricted,
        "restricted": restricted,
        "runtime": identity,
    }
    return evidence, _render(doc, evidence, families, store, jobs)


def _analysis_row(store: Store, analysis_id: str) -> dict:
    try:
        return store.get_analysis(analysis_id)
    except NotFound:
        raise ReportError(f"Analysis {analysis_id} not found.") from None


def _analysis(store: Store, jobs, analysis_id: str, as_of: projects.AsOf) -> dict:
    """An analysis the report rests on, checked against its record and the store: it must have
    succeeded by then. With the runs and experiment trials it read, by their result hashes."""
    row = _analysis_row(store, analysis_id)
    if row["created_at"] > as_of.time or projects.as_of_status(row, as_of) != "succeeded":
        raise ReportError(f"Analysis {analysis_id} had not succeeded by {as_of.time}.")
    folder = jobs.job_dir("analysis", analysis_id)
    problem = runtime.verify_outputs(folder)
    if problem:
        raise ReportError(f"Analysis {analysis_id}: {problem}")
    record = json.loads((folder / "record.json").read_text())
    if record["result_sha256"] != row["result_sha256"] or record["result_sha256"] != sha256_hex(canonical_json(record["outputs"])):
        raise ReportError(f"Analysis {analysis_id}: its record does not match the store.")
    result = json.loads((folder / "result.json").read_text())
    read, runs = set(record["datasets"]), {}
    for given in record["inputs"].values():  # and the datasets of the runs it read
        for run_id in [given["run"]] if "run" in given else _trial_runs(store, given["experiment"]) if given["kind"] == "experiment" else []:
            problem = runtime.verify_outputs(jobs.run_dir(run_id))
            if problem:
                raise ReportError(f"Run {run_id}, which analysis {analysis_id} read: {problem}")
            run_record = json.loads((jobs.run_dir(run_id) / "record.json").read_text())
            read |= set(run_record["datasets"])
            runs[run_id] = run_record["result_sha256"]
    return {
        "id": analysis_id,
        "name": row["name"],
        "method": row["method"],
        "result_sha256": record["result_sha256"],
        "outputs": {path: {"sha256": digest, "rights": result["outputs"].get(path, "aggregates")} for path, digest in record["outputs"].items()},
        "evidence_class": result["evidence_class"],
        "cutoff": result["cutoff"],
        "datasets": sorted(read),
        "runs": runs,
        "experiments": sorted({g["experiment"] for g in record["inputs"].values() if g["kind"] == "experiment"}),
    }


def _trial_runs(store: Store, experiment_id: str) -> list[str]:
    return [run["id"] for run in store.trial_runs(experiment_id) if run["status"] == "succeeded"]


def _dataset(manifest) -> dict:
    if manifest is None:
        return {"name": "(missing)", "kind": None, "source": None, "evidence_class": "synthetic", "redistributable": False, "data_sha256": None}
    return {
        "name": manifest.name,
        "data_sha256": manifest.data_sha256,
        "kind": datasets.KINDS[manifest.kind],
        "source": manifest.source,
        "evidence_class": manifest.evidence_class,
        "redistributable": manifest.redistributable,
    }


def _label(family: dict) -> str:
    return "compromised" if family["compromised"] else "confirmatory" if family["confirmatory"] else "post hoc"


def _evaluated_class(status: dict, analyses: dict) -> str | None:
    """The weakest evidence class of a family's evaluations shown, or None if none has succeeded."""
    evaluated = [analyses[e["analysis"]["id"]]["evidence_class"] for e in status["evaluations"] if e["analysis"] and e["analysis"]["id"] in analyses]
    return datasets.weakest(evaluated) if evaluated else None


def _family(status: dict, analyses: dict) -> dict:
    return {
        "name": status["document"].name,
        "sha256": status["sha256"],
        "registered_at": status["registered_at"],
        "confirmatory": status["confirmatory"],
        "compromised": status["compromised"],
        "complete": status["complete"],
        "correction": status["document"].correction,
        "alpha": status["document"].alpha,
        "withdrawals": [w["hypothesis"] for w in status["withdrawals"]],
        "evaluations": {e["hypothesis"]: e["analysis"]["id"] if e["analysis"] else None for e in status["evaluations"]},
        "outcomes": status["outcomes"],
        "evidence_class": _evaluated_class(status, analyses),
    }


def _finding(block, analyses: dict, families: dict) -> dict:
    if block.kind == "family":
        status = families[block.family]
        return {"family": block.family, "label": _label(status), "evidence_class": _evaluated_class(status, analyses)}
    return {"analysis": block.analysis, "name": getattr(block, "name", None), "evidence_class": analyses[block.analysis]["evidence_class"]}


# Rendering. Everything shown comes from the evidence or from artefacts checked against their hashes.

STYLE = """body{font-family:'DejaVu Sans',Arial,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;color:#1f2933}
table{border-collapse:collapse;margin:0.5rem 0}td,th{border:1px solid #d9dee3;padding:0.2rem 0.5rem;text-align:left;font-size:0.9rem}
.meta,.hash{color:#616e7c;font-size:0.85rem}.label{font-weight:bold}.warning{color:#9b2c2c}"""
POLICY = "default-src 'none'; style-src 'unsafe-inline'"


def _render(doc: ReportDocument, evidence: dict, families: dict, store: Store, jobs) -> str:
    project = evidence["project"]["document"]
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">',
        f"<title>{escape(doc.title)}</title><style>{STYLE}</style></head><body>",
        f"<h1>{escape(doc.title)}</h1>",
        f'<p class="meta">{escape(project["name"])}: {escape(project["question"])} Confirmation start {escape(project["confirmation_start"])}. '
        f"Evidence as of {escape(evidence['as_of']['time'])} (read {evidence['as_of']['read']}).</p>",
        f'<p class="label">{_rights(evidence)} Weakest evidence of its findings: {escape(str(evidence["evidence_class"] or "none"))}.</p>',
    ]
    for section in doc.sections:
        parts.append(f"<h2>{escape(section.heading)}</h2>")
        for block in section.blocks:
            parts += _block(block, evidence, families, jobs)
    parts += _evidence_section(evidence)
    return "\n".join(parts + ["</body></html>"]) + "\n"


def _rights(evidence: dict) -> str:
    if evidence["redistributable"]:
        return "Redistributable."
    return "Not redistributable: it includes " + escape(", ".join(evidence["restricted"])) + "."


def _block(block, evidence: dict, families: dict, jobs) -> list[str]:
    if block.kind == "text":
        return [f"<p>{escape(paragraph.strip())}</p>" for paragraph in block.text.split("\n\n") if paragraph.strip()]
    if block.kind == "family":
        return _family_block(block.family, evidence["families"][block.family], families[block.family], evidence["analyses"])
    analysis = evidence["analyses"][block.analysis]
    folder = jobs.job_dir("analysis", block.analysis)
    caption = f'<p class="hash">{escape(analysis["name"])}: {escape(analysis["method"])}, evidence {escape(analysis["evidence_class"])}, '
    caption += f"result {analysis['result_sha256']}"
    if block.kind == "values":
        result = json.loads((folder / "result.json").read_text())
        rows = "".join(f"<tr><th>{escape(k)}</th><td>{escape(json.dumps(v, sort_keys=True))}</td></tr>" for k, v in sorted(result["values"].items()))
        notes = [f'<p class="warning">{escape(w)}</p>' for w in result["warnings"]] + [
            f'<p class="meta">Assumes: {escape(a)}</p>' for a in result["assumptions"]
        ]
        return [caption + "</p>", f"<table>{rows}</table>", *notes]
    path = f"{'figures' if block.kind == 'figure' else 'tables'}/{block.name}.{'svg' if block.kind == 'figure' else 'csv'}"
    if path not in analysis["outputs"]:
        raise ReportError(f"Analysis {block.analysis} has no {block.kind} {block.name}.")
    output = analysis["outputs"][path]
    shown = caption + f", {escape(path)} {output['sha256']} ({output['rights']})</p>"
    if block.kind == "figure":
        return [shown, (folder / path).read_text()]
    rows = list(csv.reader(io.StringIO((folder / path).read_text())))
    body = "".join(
        "<tr>" + "".join(f"<{'th' if i == 0 else 'td'}>{escape(cell)}</{'th' if i == 0 else 'td'}>" for cell in row) + "</tr>"
        for i, row in enumerate(rows[: TABLE_ROWS + 1])
    )
    more = [f'<p class="meta">{len(rows) - 1 - TABLE_ROWS} more rows in the analysis.</p>'] if len(rows) - 1 > TABLE_ROWS else []
    return [shown, f"<table>{body}</table>", *more]


def _family_block(family_id: str, family: dict, status: dict, analyses: dict) -> list[str]:
    label = {
        "confirmatory": "Confirmatory",
        "compromised": "Compromised (test period read outside its evaluations)",
        "post hoc": "Post hoc",
    }[_label(family)]
    head = f'<p class="label">{escape(family["name"])}: {label}; {escape(family["correction"])} at {family["alpha"]}; '
    head += f"{'complete' if family['complete'] else 'not complete: conclusions wait for every hypothesis'}.</p>"
    cells = ("hypothesis", "statement", "direction", "state", "estimate", "p", "adjusted p", "supported", "result")
    rows = ["<tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>"]
    statements = {h.id: h for h in status["document"].hypotheses}
    for outcome in family["outcomes"]:
        h = statements[outcome["hypothesis"]]
        evaluated = family["evaluations"].get(h.id)
        values = (
            h.id,
            h.statement,
            h.direction,
            outcome["state"],
            outcome["estimate"],
            outcome["p_value"],
            outcome.get("adjusted_p_value"),
            outcome.get("supported"),
            analyses[evaluated]["result_sha256"] if evaluated in analyses else None,
        )
        rows.append("<tr>" + "".join(f"<td>{escape('' if v is None else str(v))}</td>" for v in values) + "</tr>")
    hash_line = f'<p class="hash">family {family_id} {family["sha256"]}, registered {escape(family["registered_at"])}, '
    hash_line += f"evidence {escape(family['evidence_class'] or 'none: no evaluation has succeeded')}</p>"
    return [head, f"<table>{''.join(rows)}</table>", hash_line]


def _evidence_section(evidence: dict) -> list[str]:
    datasets_rows = "".join(
        f"<tr><td>{escape(d['name'])}</td><td>{escape(str(d['kind']))}</td><td>{escape(str(d['evidence_class']))}</td>"
        f'<td>{"yes" if d["redistributable"] else "no"}</td><td class="hash">{escape(i)}</td></tr>'
        for i, d in sorted(evidence["datasets"].items())
    )
    horizons = "".join(
        f'<tr><td class="hash">{escape(i)}</td><td>{"in full" if h["full"] else escape(str(h["read_to"] or "never"))}</td>'
        f"<td>{'yes' if h['test_period_read'] else 'no'}</td></tr>"
        for i, h in sorted(evidence["horizons"].items())
    )
    families = "".join(
        f"<li>{escape(f['name'])}: {'confirmatory' if f['confirmatory'] else 'post hoc'}{', compromised' if f['compromised'] else ''}, "
        f"{'complete' if f['complete'] else 'not complete'}</li>"
        for f in evidence["families"].values()
    )
    counts = ", ".join(f"{n} {status}" for status, n in evidence["project_analyses"].items()) or "none"
    return [
        "<h2>Evidence</h2>",
        "<table><tr><th>dataset</th><th>kind</th><th>evidence</th><th>redistributable</th><th>ID</th></tr>" + datasets_rows + "</table>",
        "<p>Read horizons of the datasets in scope:</p>",
        "<table><tr><th>dataset</th><th>read</th><th>test period read</th></tr>" + horizons + "</table>",
        f"<p>Analyses in the project: {escape(counts)}. Every family registered in it:</p><ul>{families}</ul>",
        f'<p class="hash">qrs {escape(evidence["runtime"]["identity"]["qrs"])}, source {evidence["runtime"]["identity"]["qrs_source_sha256"]}, '
        f"SciPy {escape(evidence['runtime']['scipy'])}</p>",
    ]

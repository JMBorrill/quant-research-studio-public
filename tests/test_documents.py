"""Documents datasets (Phase 4B workstream 1): document versions, such as news or reports, imported
from CSV with the observations' row-numbered rejections and estimate rules, stored immutably, and
never readable by a strategy."""

import json
import re
import tempfile
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from conftest import client_for
from qrs import cli, datasets, graph, importer, registry
from qrs.contracts import DocumentsDeclaration
from qrs.datasets import DatasetError
from qrs.hashing import sha256_hex
from test_observations import as_of_strategy, import_fixture

# Three documents: A in two versions (a correction published later), B with a quoted multi-line,
# non-ASCII body published in Tokyo time between A's two versions, C published exactly at midnight
# UTC, with whitespace around its text that is kept.
DOCUMENTS_CSV = '''Id,Published,Version,Headline,Body,Link,Day
A,2024-11-26T09:30:00-05:00,first,Rates hold,"The committee kept rates at 4.5%.",https://example.com/a,2024-11-26
B,2024-11-27T04:00:00+09:00,first,"Yen, ""steady""","Line one: 円相場は安定。
Line two, quoted ""as is"".",https://example.com/b,2024-11-26
A,2024-11-26T15:00:00-05:00,corrected,Rates hold,"The committee kept rates at 4.25%.",https://example.com/a,2024-11-26
C,2024-11-28T00:00:00+00:00,first,Holiday,"  Markets closed.
",,2024-11-28
'''.encode()


def declaration(**changes) -> DocumentsDeclaration:
    declared = {
        "kind": "qrs/documents",
        "name": "Fixture news",
        "columns": {"id": "Id", "published": "Published", "text": "Body", "title": "Headline", "revision": "Version", "url": "Link", "event": "Day"},
        "event_format": "%Y-%m-%d",
        "evidence_class": "retrospective",
        "source": "tests",
        "redistributable": True,
        "hosted_processing": False,
    }
    return DocumentsDeclaration.model_validate({**declared, **changes})


def test_documents_are_stored_one_row_per_version_with_their_text_exactly_and_its_hash(tmp_path):
    dataset_id = importer.import_documents(tmp_path, DOCUMENTS_CSV, declaration())
    manifest, frame = datasets.load_documents(tmp_path, dataset_id)
    assert (manifest.kind, manifest.rows, manifest.documents, manifest.estimated_rows) == ("qrs/documents", 4, 3, 0)
    assert (manifest.hosted_processing, manifest.redistributable, manifest.source_sha256) == (False, True, sha256_hex(DOCUMENTS_CSV))
    assert list(frame.columns) == datasets.DOCUMENT_FIELDS
    # In publication order, not by document: B (19:00 UTC on the 26th) between A's two versions.
    assert list(zip(frame["document"], frame["revision"], strict=True)) == [("A", "first"), ("B", "first"), ("A", "corrected"), ("C", "first")]
    assert list(frame["published"]) == list(pd.to_datetime(["2024-11-26T14:30Z", "2024-11-26T19:00Z", "2024-11-26T20:00Z", "2024-11-28T00:00Z"]))
    body = 'Line one: 円相場は安定。\nLine two, quoted "as is".'
    assert frame["text"][1] == body and frame["title"][1] == 'Yen, "steady"'
    assert frame["text"][3] == "  Markets closed.\n"  # kept exactly, whitespace and all
    assert list(frame["text_sha256"]) == [sha256_hex(text.encode()) for text in frame["text"]]
    assert list(frame["url"]) == ["https://example.com/a", "https://example.com/b", "https://example.com/a", ""]
    assert list(frame["event"]) == list(pd.to_datetime(["2024-11-26", "2024-11-26", "2024-11-26", "2024-11-28"]))
    assert not frame["estimated"].any()


def test_without_an_event_column_a_documents_event_is_the_utc_date_of_its_publication(tmp_path):
    csv = b"Id,Published,Body\nB,2024-11-27T08:00:00+09:00,text\nC,2024-11-27T23:59:59-05:00,text\n"
    dataset_id = importer.import_documents(tmp_path, csv, declaration(columns={"id": "Id", "published": "Published", "text": "Body"}, event_format=None))
    _, frame = datasets.load_documents(tmp_path, dataset_id)
    assert list(frame["event"]) == list(pd.to_datetime(["2024-11-26", "2024-11-28"]))
    assert list(frame["revision"]) == list(frame["title"]) == list(frame["url"]) == ["", ""]


@pytest.mark.parametrize("evidence_class", ["synthetic", "retrospective", "point_in_time"])
def test_imports_refuse_bad_rows_by_number_whatever_the_evidence_class(tmp_path, evidence_class):
    csv = b"""Id,Published,Version,Headline,Body,Link,Day,Estimated
A,2024-11-26T09:30:00-05:00,first,t,body,,2024-11-26,false
,2024-11-26T10:00:00-05:00,first,t,body,,2024-11-26,false
B,,first,t,body,,2024-11-26,false
C,2024-11-26T10:00:00,first,t,body,,2024-11-26,false
D,9999-12-31T23:59:59-05:00,first,t,body,,2024-11-26,false
E,2024-11-26T10:00:00-05:00,first,t," ",,2024-11-26,false
A,2024-11-26T11:00:00-05:00,first,t,body,,2024-11-26,false
A,2024-11-26T14:30:00+00:00,second,t,body,,2024-11-26,false
F,2024-11-26T10:00:00-05:00,first,t,body,,26/11/2024,false
G,2024-11-26T10:00:00-05:00,first,t,body,,2024-11-26,maybe
"""
    columns = {**declaration().columns.model_dump(), "estimated": "Estimated"}
    with pytest.raises(DatasetError) as refused:
        importer.import_documents(tmp_path, csv, declaration(columns=columns, evidence_class=evidence_class))
    assert str(refused.value).splitlines() == [
        "The file was rejected:",
        "row 3: id is missing",
        "row 4: published is missing",
        "row 5: published '2024-11-26T10:00:00' has no UTC offset",
        "row 6: published '9999-12-31T23:59:59-05:00' is out of range",
        "row 7: text is missing",
        "row 8: repeats the document and revision of row 2",
        "row 9: repeats the document and publication time of row 2",
        "row 10: event '26/11/2024' does not match the format '%Y-%m-%d'",
        "row 11: estimated must be true or false, not 'maybe'",
    ]
    assert datasets.list_manifests(tmp_path) == {}


def test_versions_need_revision_labels_and_an_event_column_needs_its_format(tmp_path):
    unlabelled = b"Id,Published,Body\nA,2024-11-26T09:30:00-05:00,one\nA,2024-11-26T15:00:00-05:00,two\n"
    with pytest.raises(DatasetError, match=re.escape("row 3: repeats the document (each version needs a revision label) of row 2")):
        importer.import_documents(tmp_path, unlabelled, declaration(columns={"id": "Id", "published": "Published", "text": "Body"}, event_format=None))
    with pytest.raises(DatasetError, match="Declare an event column and its event_format together, or neither."):
        importer.import_documents(tmp_path, DOCUMENTS_CSV, declaration(event_format=None))
    with pytest.raises(DatasetError, match="must be a different header"):
        importer.import_documents(tmp_path, DOCUMENTS_CSV, declaration(columns={**declaration().columns.model_dump(), "title": "Body"}))
    with pytest.raises(DatasetError, match="The file has no documents."):
        importer.import_documents(tmp_path, DOCUMENTS_CSV.splitlines(keepends=True)[0], declaration())


def test_hosted_processing_must_be_declared(tmp_path):
    declared = declaration().model_dump()
    del declared["hosted_processing"]
    with pytest.raises(ValidationError, match="hosted_processing"):
        DocumentsDeclaration.model_validate(declared)


def test_estimated_timestamps_cannot_be_point_in_time_and_are_kept_with_how_they_were_made(tmp_path):
    csv = DOCUMENTS_CSV.replace(b",Day\n", b",Day,Estimated\n").replace(b"2024-11-26\n", b"2024-11-26,false\n").replace(b"2024-11-28\n", b"2024-11-28,true\n")
    columns = {**declaration().columns.model_dump(), "estimated": "Estimated"}
    with pytest.raises(
        DatasetError, match=r"1 row\(s\) have estimated publication timestamps \(first row 6\), so the data is retrospective, not point_in_time"
    ):
        importer.import_documents(tmp_path, csv, declaration(columns=columns, evidence_class="point_in_time", estimates="first seen by a crawler"))
    with pytest.raises(DatasetError, match="declare how they were estimated"):
        importer.import_documents(tmp_path, csv, declaration(columns=columns))
    with pytest.raises(DatasetError, match="no row is marked estimated"):
        importer.import_documents(tmp_path, DOCUMENTS_CSV, declaration(estimates="first seen by a crawler"))
    dataset_id = importer.import_documents(tmp_path, csv, declaration(columns=columns, estimates="first seen by a crawler"))
    manifest, frame = datasets.load_documents(tmp_path, dataset_id)
    assert (manifest.evidence_class, manifest.estimated_rows, manifest.estimates) == ("retrospective", 1, "first seen by a crawler")
    assert list(frame["estimated"]) == [False, False, False, True]


def test_the_same_file_gives_the_same_id_and_a_changed_dataset_is_refused(tmp_path):
    first = importer.import_documents(tmp_path, DOCUMENTS_CSV, declaration())
    assert importer.import_documents(tmp_path, DOCUMENTS_CSV, declaration()) == first
    assert importer.import_documents(tmp_path, DOCUMENTS_CSV, declaration(hosted_processing=True)) != first
    data = datasets.directory(tmp_path, first) / "documents.parquet"
    data.chmod(0o644)
    data.write_bytes(data.read_bytes() + b"\0")
    with pytest.raises(DatasetError, match="changed after publication"):
        datasets.load_documents(tmp_path, first)


def test_no_strategy_can_read_a_documents_dataset(tmp_path):
    bars_id, observations_id = import_fixture(tmp_path)
    documents_id = importer.import_documents(tmp_path, DOCUMENTS_CSV, declaration())
    issues = graph.check_data(as_of_strategy(documents_id, documents_id), tmp_path)
    assert [(i.node, i.message) for i in issues][:2] == [
        ("bars", "dataset_id: the dataset holds documents, not bars."),
        ("rate", "dataset_id: the dataset holds documents, not observations."),
    ]
    with pytest.raises(DatasetError, match="holds documents, not observations"):
        datasets.load_observations(tmp_path, documents_id)
    assert graph.check_data(as_of_strategy(bars_id, observations_id), tmp_path) == []


def test_qrs_import_and_the_upload_endpoint_import_documents_alike(tmp_path, capsys):
    csv, declared = tmp_path / "news.csv", tmp_path / "news.json"
    csv.write_bytes(DOCUMENTS_CSV)
    declared.write_text(declaration().model_dump_json())
    assert cli.main(["import", str(csv), str(declared), "--home", str(tmp_path / "cli")]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[1] == "documents: 3 (4 versions), 0 with estimated publication times"
    client = client_for(tmp_path / "api")
    response = client.post(
        "/api/datasets/import", params={"declaration": declaration().model_dump_json()}, content=DOCUMENTS_CSV, headers={"Content-Type": "text/csv"}
    )
    assert response.status_code == 200, response.text
    assert printed[0] == f"dataset  {response.json()['id']}"
    listed = client.get("/api/datasets").json()
    assert [(d["kind"], d["documents"], d["hosted_processing"]) for d in listed] == [("qrs/documents", 3, False)]
    missing = json.loads(declaration().model_dump_json())
    del missing["hosted_processing"]
    refused = client.post("/api/datasets/import", params={"declaration": json.dumps(missing)}, content=DOCUMENTS_CSV, headers={"Content-Type": "text/csv"})
    assert refused.status_code == 422
    assert refused.json()["detail"][0]["loc"] == ["query", "declaration", "hosted_processing"]


def test_a_long_report_is_imported_and_a_text_over_the_upload_limit_is_refused_by_row(tmp_path):
    bare = declaration(columns={"id": "Id", "published": "Published", "text": "Body"}, event_format=None)
    report = "x" * 500_000  # longer than the csv module's default field limit
    dataset_id = importer.import_documents(tmp_path, f"Id,Published,Body\nA,2024-11-26T09:30:00-05:00,{report}\n".encode(), bare)
    assert datasets.load_documents(tmp_path, dataset_id)[1]["text"][0] == report
    too_long = "x" * (importer.MAX_FIELD_CHARS + 1)
    csv = f"Id,Published,Body\nA,2024-11-26T09:30:00-05:00,short\nB,2024-11-26T10:00:00-05:00,{too_long}\n".encode()
    with pytest.raises(DatasetError, match=r"^Row 3 is not valid CSV: field larger than field limit"):
        importer.import_documents(tmp_path, csv, bare)


def test_a_record_spanning_lines_is_reported_by_its_first_line(tmp_path):
    csv = b'Id,Published,Body\nA,2024-11-26T09:30:00-05:00,"one\ntwo"\n\nB,2024-11-26T10:00:00,"three\nfour\nfive"\n'
    with pytest.raises(DatasetError, match="^The file was rejected:\nrow 5: published '2024-11-26T10:00:00' has no UTC offset$"):
        importer.import_documents(tmp_path, csv, declaration(columns={"id": "Id", "published": "Published", "text": "Body"}, event_format=None))


def test_existing_observation_and_bars_ids_are_unchanged():
    # The Stage 4 observations fixture, imported by the Phase 4A code (5624dda), gave these IDs.
    assert import_fixture(Path(tempfile.mkdtemp())) == (
        "sha256:6a4681d12c7ffdc766290970ac6e5413c38440744e55f65ff6e451332eaed8cb",
        "sha256:2fcbf3cf91684c5bacd85b615a0277554bdaa90e7bd3a72a0703071269ca54f1",
    )


def test_no_operator_takes_a_documents_dataset():
    # So neither a strategy nor any studio picker, which lists datasets of a parameter's kind, offers one.
    kinds = {op.dataset_kind(param) for op in registry.all_operators() for param in op.dataset_params()}
    assert kinds == {datasets.BARS, datasets.OBSERVATIONS}

"""Stage 5 workstream 8: reports and evidence trails. A report is deterministic HTML and evidence,
fixed at a point in the store's history; `verify` finds any changed artefact and is stable as the
store grows; every number traces to an output hash; text is escaped; and rights are labelled, with
a shareable report refused when it includes data whose terms forbid redistribution."""

import json
import re
from datetime import date

import pytest

from conftest import client_for
from qrs import cli
from qrs.contracts import ImportDeclaration, ReportDocument
from qrs.importer import import_csv
from test_analysis import document, wait_analysis
from test_experiments import holdout_start
from test_observations import BARS_CSV, bars_declaration
from test_projects import family_doc, hypothesis, new_project, synthetic

HOSTILE = '<script>alert("x")</script> & <img src=x onerror=alert(1)>'


def analysed(client, project_id: str, inputs: dict, method_id: str) -> str:
    queued = client.post("/api/analyses", json=document(inputs, method_id, project=project_id).model_dump(mode="json"))
    assert queued.status_code == 200, queued.text
    assert wait_analysis(client, queued.json()["id"])["status"] == "succeeded"
    return queued.json()["id"]


def report_doc(project_id: str, values: str, histogram: str, family: str) -> dict:
    blocks = [
        {"kind": "text", "text": f"Why this matters.\n\n{HOSTILE}"},
        {"kind": "values", "analysis": values},
        {"kind": "figure", "analysis": histogram, "name": "histogram"},
        {"kind": "table", "analysis": histogram, "name": "histogram"},
        {"kind": "family", "family": family},
    ]
    return {"project": project_id, "title": f"Findings {HOSTILE}", "sections": [{"heading": "Results", "blocks": blocks}]}


@pytest.fixture
def research(home):
    """A project with a summary, a distribution and an evaluated confirmatory family."""
    with client_for(home) as client:
        dataset, start = synthetic(client), holdout_start(home)
        project_id = new_project(client, [dataset], start)
        bars = {"values": {"kind": "bars", "dataset": dataset}}
        values = analysed(client, project_id, bars, "qrs.summary")
        histogram = analysed(client, project_id, bars, "qrs.distribution")
        family = client.post("/api/families", json=family_doc(project_id, [hypothesis(bars)])).json()
        evaluation = client.post(f"/api/families/{family['id']}/evaluate", json={"hypothesis": "mean"}).json()["evaluations"][0]
        wait_analysis(client, evaluation["analysis"]["id"])
        yield client, report_doc(project_id, values, histogram, family["id"]), dataset


def test_a_report_is_self_contained_escaped_and_traces_every_number(research):
    client, doc, dataset = research
    built = client.post("/api/reports", json=doc)
    assert built.status_code == 200, built.text
    report = built.json()
    html = client.get(f"/api/reports/{report['id']}/html")
    assert "sandbox" in html.headers["content-security-policy"] and html.headers["content-type"].startswith("text/html")
    text = html.text
    assert "<script" not in text and "<img" not in text
    assert "&lt;script&gt;" in text and "&lt;img src=x onerror=alert(1)&gt;" in text
    assert not re.search(r"""(src|href)=["']?(https?:|//)""", text) and "<svg" in text and "Content-Security-Policy" in text
    evidence = report["evidence"]
    for analysis in evidence["analyses"].values():  # each number shown carries its result's hash
        assert analysis["result_sha256"] in text and all(len(o["sha256"]) == 64 for o in analysis["outputs"].values())
    assert evidence["analyses"][doc["sections"][0]["blocks"][2]["analysis"]]["outputs"]["figures/histogram.svg"]["rights"] == "aggregates"
    family = evidence["families"][doc["sections"][0]["blocks"][4]["family"]]
    assert family["confirmatory"] and family["complete"] and family["outcomes"][0]["adjusted_p_value"] is not None
    assert evidence["datasets"][dataset]["redistributable"] and evidence["redistributable"]
    assert evidence["project_analyses"] == {"succeeded": 3} and evidence["horizons"][dataset]["test_period_read"]
    assert [f["evidence_class"] for f in evidence["findings"]] == ["synthetic"] * 4 and evidence["findings"][3]["label"] == "confirmatory"
    assert evidence["runtime"]["identity"]["qrs_source_sha256"] and evidence["runtime"]["scipy"]


def test_verify_is_stable_as_the_store_grows_and_finds_any_changed_artefact(research, home):
    client, doc, dataset = research
    report = client.post("/api/reports", json=doc).json()
    assert client.post(f"/api/reports/{report['id']}/verify").json() == {"verified": True, "problems": []}
    # The store grows: another analysis, a note and another family, none of which the report saw.
    analysed(client, doc["project"], {"values": {"kind": "bars", "dataset": dataset}}, "qrs.summary")
    client.post(f"/api/projects/{doc['project']}/notes", json={"text": "later"})
    client.post("/api/families", json=family_doc(doc["project"], [hypothesis({"values": {"kind": "bars", "dataset": dataset}})], confirmatory=False))
    assert client.post(f"/api/reports/{report['id']}/verify").json()["verified"]
    # Any changed artefact is found: a figure the report embeds, and the report's own files.
    figure = home / "analyses" / doc["sections"][0]["blocks"][2]["analysis"] / "figures" / "histogram.svg"
    original = figure.read_text()
    figure.chmod(0o644)
    figure.write_text(original.replace("Distribution", "Distorted"))
    problems = client.post(f"/api/reports/{report['id']}/verify").json()["problems"]
    assert any("figures/histogram.svg does not match the hash" in p for p in problems)
    figure.write_text(original)
    evidence = home / "reports" / report["id"] / "evidence.json"
    evidence.write_text(evidence.read_text().replace('"complete": true', '"complete": false'))
    problems = client.post(f"/api/reports/{report['id']}/verify").json()["problems"]
    assert "evidence.json does not match the hash in record.json." in problems and any("evidence differs" in p for p in problems)


def test_reports_through_the_cli_and_rights(research, home, tmp_path):
    client, doc, _ = research
    (tmp_path / "report.json").write_text(json.dumps(doc))
    assert cli.main(["report", "build", str(tmp_path / "report.json"), "--home", str(home), "--out", str(tmp_path / "r")]) == 0
    assert cli.main(["report", "verify", str(tmp_path / "r"), "--home", str(home)]) == 0
    (tmp_path / "r" / "report.html").write_text("changed")
    assert cli.main(["report", "verify", str(tmp_path / "r"), "--home", str(home)]) == 1
    # Data whose terms forbid redistribution: the report says so, and a shareable one is refused.
    licensed = import_csv(home, BARS_CSV, ImportDeclaration.model_validate(bars_declaration(redistributable=False)))
    restricted = new_project(client, [licensed], date(2024, 12, 2))
    summary = analysed(client, restricted, {"values": {"kind": "bars", "dataset": licensed}}, "qrs.summary")
    body = {"project": restricted, "title": "Licensed", "sections": [{"heading": "S", "blocks": [{"kind": "values", "analysis": summary}]}]}
    built = client.post("/api/reports", json=body).json()
    assert not built["evidence"]["redistributable"] and built["evidence"]["restricted"] == ["NYSE sessions"]
    assert "Not redistributable: it includes NYSE sessions." in client.get(f"/api/reports/{built['id']}/html").text
    (tmp_path / "licensed.json").write_text(json.dumps(body))
    assert cli.main(["report", "build", str(tmp_path / "licensed.json"), "--home", str(home), "--out", str(tmp_path / "l"), "--shareable"]) == 1
    assert not (tmp_path / "l").exists()
    # The same through a run: an analysis of its equity read the run's data.
    from conftest import new_strategy, start, wait
    from qrs import datasets

    frame = datasets.synthetic_bars(7, 300)
    hidden = datasets.save(
        home, frame, name="Licensed synthetic", instrument=datasets.SYNTHETIC_INSTRUMENT, evidence_class="synthetic", source="t", redistributable=False
    )
    run = wait(client, start(client, new_strategy(client, hidden))["id"])
    equity = analysed(client, new_project(client, [hidden], frame.index[200].date()), {"values": {"kind": "equity", "run": run["id"]}}, "qrs.summary")
    through = client.post(
        "/api/reports",
        json=body
        | {
            "project": client.get(f"/api/analyses/{equity}").json()["project"],
            "sections": [{"heading": "S", "blocks": [{"kind": "values", "analysis": equity}]}],
        },
    ).json()
    assert not through["evidence"]["redistributable"] and through["evidence"]["restricted"] == ["Licensed synthetic"]


def test_a_report_refuses_unfinished_analyses_and_families_of_other_projects(research, home):
    client, doc, dataset = research
    other = new_project(client, [dataset], holdout_start(home))
    foreign = client.post("/api/families", json=family_doc(other, [hypothesis({"values": {"kind": "bars", "dataset": dataset}})], confirmatory=False)).json()
    body = {"project": doc["project"], "title": "t", "sections": [{"heading": "s", "blocks": [{"kind": "family", "family": foreign["id"]}]}]}
    refused = client.post("/api/reports", json=body)
    assert refused.status_code == 422 and "is not one of the project's" in refused.json()["detail"]
    body["sections"][0]["blocks"] = [{"kind": "figure", "analysis": doc["sections"][0]["blocks"][1]["analysis"], "name": "histogram"}]
    assert "has no figure histogram" in client.post("/api/reports", json=body).json()["detail"]


# Regressions from the workstream's review.


def test_a_family_with_a_failed_evaluation_is_reported_as_p_1(research, home):
    client, doc, dataset = research
    from qrs.store import Store

    store = Store(home / "qrs.sqlite")
    family_id = doc["sections"][0]["blocks"][4]["family"]
    failing = client.post(
        "/api/families", json=family_doc(doc["project"], [hypothesis({"values": {"kind": "bars", "dataset": dataset}})], confirmatory=False)
    ).json()
    evaluation = client.post(f"/api/families/{failing['id']}/evaluate", json={"hypothesis": "mean"}).json()["evaluations"][0]
    wait_analysis(client, evaluation["analysis"]["id"])
    store.update_job("analysis", evaluation["analysis"]["id"], status="failed")  # as a worker failure would leave it
    body = {**doc, "sections": [{"heading": "Families", "blocks": [{"kind": "family", "family": failing["id"]}, {"kind": "family", "family": family_id}]}]}
    built = client.post("/api/reports", json=body)
    assert built.status_code == 200, built.text
    shown = built.json()["evidence"]["families"][failing["id"]]
    assert shown["outcomes"][0]["state"] == "failed" and shown["outcomes"][0]["adjusted_p_value"] == 1.0 and shown["evidence_class"] is None
    html = client.get(f"/api/reports/{built.json()['id']}/html").text
    assert "Post hoc" in html and "none: no evaluation has succeeded" in html and "Weakest evidence of its findings: synthetic" in html


def test_every_familys_numbers_are_checked_and_only_the_projects_analyses_are_shown(research, home):
    client, doc, dataset = research
    family_id = doc["sections"][0]["blocks"][4]["family"]
    evaluation = client.get(f"/api/families/{family_id}").json()["evaluations"][0]["analysis"]["id"]
    result = home / "analyses" / evaluation / "result.json"
    original = result.read_text()
    result.chmod(0o644)
    result.write_text(original.replace('"p_value": ', '"p_value": 1e-12, "was": '))
    unreferenced = {**doc, "sections": [{"heading": "Only text", "blocks": [{"kind": "text", "text": "No family is shown, but every family is listed."}]}]}
    refused = client.post("/api/reports", json=unreferenced)
    assert refused.status_code == 422 and "result.json does not match the hash" in refused.json()["detail"]
    result.write_text(original)
    outside = client.post("/api/analyses", json=document({"values": {"kind": "bars", "dataset": dataset}}).model_dump(mode="json")).json()
    wait_analysis(client, outside["id"])
    body = {**doc, "sections": [{"heading": "s", "blocks": [{"kind": "values", "analysis": outside["id"]}]}]}
    refused = client.post("/api/reports", json=body)
    assert refused.status_code == 422 and "is not one of the project's" in refused.json()["detail"]


def test_runs_read_are_in_the_evidence_with_their_hashes(research, home, tmp_path):
    client, doc, dataset = research
    from conftest import new_strategy, start, wait

    run = wait(client, start(client, new_strategy(client, dataset))["id"])
    analysis = client.post(
        "/api/analyses", json=document({"values": {"kind": "equity", "run": run["id"]}}, project=doc["project"]).model_dump(mode="json")
    ).json()
    wait_analysis(client, analysis["id"])
    body = {**doc, "sections": [{"heading": "s", "blocks": [{"kind": "values", "analysis": analysis["id"]}]}]}
    evidence = client.post("/api/reports", json=body).json()["evidence"]
    assert evidence["analyses"][analysis["id"]]["runs"] == {run["id"]: run["result_sha256"]}
    assert evidence["datasets"][dataset]["data_sha256"] and evidence["evidence_class"] == "synthetic"
    (tmp_path / "file").write_text("")
    (tmp_path / "r.json").write_text(json.dumps(body))
    assert cli.main(["report", "build", str(tmp_path / "r.json"), "--home", str(home), "--out", str(tmp_path / "file")]) == 1


def test_a_report_built_while_an_evaluation_is_running_shows_it_running_and_verifies_after(research, home):
    client, doc, dataset = research
    from qrs import projects, reports
    from qrs.store import Store

    store, jobs = Store(home / "qrs.sqlite"), client.app.state.jobs
    family = client.post(
        "/api/families", json=family_doc(doc["project"], [hypothesis({"values": {"kind": "bars", "dataset": dataset}})], confirmatory=False)
    ).json()
    evaluation = client.post(f"/api/families/{family['id']}/evaluate", json={"hypothesis": "mean"}).json()["evaluations"][0]
    # The evaluation is stored just after its analysis is queued: requested, not finished.
    moment = projects.AsOf(store.evaluations(family["id"])[0]["requested_at"], store.last_read())
    wait_analysis(client, evaluation["analysis"]["id"])
    body = ReportDocument.model_validate({**doc, "sections": [{"heading": "s", "blocks": [{"kind": "family", "family": family["id"]}]}]})
    evidence, _ = reports._assemble(store, jobs, body, moment, {"identity": {"qrs": "x", "qrs_source_sha256": "y"}, "scipy": "z"})
    assert evidence["families"][family["id"]]["outcomes"][0]["state"] == "running" and not evidence["families"][family["id"]]["complete"]


def test_a_report_whose_text_holds_a_carriage_return_verifies(home):
    with client_for(home) as client:
        project_id = new_project(client, [synthetic(client)], holdout_start(home))
        doc = {"project": project_id, "title": "T", "sections": [{"heading": "H", "blocks": [{"kind": "text", "text": "line one\r\nline two"}]}]}
        built = client.post("/api/reports", json=doc)
        assert built.status_code == 200, built.text
        assert client.post(f"/api/reports/{built.json()['id']}/verify").json()["verified"]

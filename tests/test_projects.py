"""Stage 5 workstream 4: projects, frozen families and the read history (amendment A2). A
confirmatory family is refused once the test period of a dataset in scope has been read, on every
history the plan names; its evaluation fails closed when the test period is read outside the
project's confirmatory evaluations; registrations, withdrawals, evaluations and reads are never
changed or removed; and a store from before the read history is backfilled, an unknown read counting
as a full one."""

import shutil
import sqlite3
from datetime import date

import pandas as pd
import pytest

from conftest import client_for, new_strategy, start, wait
from qrs import cli, datasets, projects
from qrs.contracts import ImportDeclaration, ProjectDocument
from qrs.importer import import_csv, import_observations
from qrs.store import Store
from test_analysis import document, wait_analysis
from test_experiments import HOLDOUT, create, experiment, finished, holdout_start
from test_observations import BARS_CSV, OBSERVATIONS_CSV, bars_declaration, observations_declaration


def project_doc(scope: list[str], start: date) -> dict:
    return ProjectDocument(name="Test", question="Does it hold?", datasets=scope, confirmation_start=start).model_dump(mode="json")


def family_doc(project_id: str, hypotheses: list[dict], confirmatory: bool = True) -> dict:
    return {"project": project_id, "name": "A", "confirmatory": confirmatory, "hypotheses": hypotheses}


def hypothesis(inputs: dict, id: str = "mean") -> dict:
    return {"id": id, "statement": "Returns are positive.", "analysis": document(inputs, "qrs.mean_test").model_dump(mode="json"), "direction": "greater"}


def bars_input(dataset: str) -> dict:
    return {"values": {"kind": "bars", "dataset": dataset}}


def new_project(client, scope: list[str], start: date) -> str:
    created = client.post("/api/projects", json=project_doc(scope, start))
    assert created.status_code == 200, created.text
    return created.json()["id"]


def synthetic(client) -> str:
    return client.get("/api/datasets").json()[0]["id"]


def test_a_project_caps_its_analyses_and_a_clean_confirmatory_family_registers(home):
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        project_id = new_project(client, [dataset], start_date)
        capped = client.post("/api/analyses", json=document(bars_input(dataset), project=project_id).model_dump(mode="json")).json()
        result = client.get(f"/api/analyses/{wait_analysis(client, capped['id'])['id']}/result").json()
        assert result["document"]["until"] == start_date.isoformat() and result["result"]["cutoff"] == start_date.isoformat()
        assert result["result"]["values"]["n"] == HOLDOUT - 1  # the sessions before the confirmation start, less the first
        registered = client.post("/api/families", json=family_doc(project_id, [hypothesis(bars_input(dataset))]))
        assert registered.status_code == 200, registered.text
        family = registered.json()
        assert family["confirmatory"] and not family["compromised"] and len(family["sha256"]) == 64
        shown = client.get(f"/api/projects/{project_id}").json()
        assert shown["horizons"][dataset] == {"reads": 1, "read_to": start_date.isoformat(), "full": False, "test_period_read": False}
        assert shown["analyses"] == {"succeeded": 1} and [f["id"] for f in shown["families"]] == [family["id"]]


@pytest.mark.parametrize("history", ["full run", "holdout evaluation", "analysis", "observations only"])
def test_confirmatory_registration_is_refused_once_the_test_period_was_read(home, history):
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        scope, tested = [dataset], hypothesis(bars_input(dataset))
        if history == "full run":
            wait(client, start(client, new_strategy(client))["id"])
        elif history == "holdout evaluation":
            created = finished(client, create(client, experiment(new_strategy(client), start_date, [("fast", "period", [5, 10])])))
            assert client.post(f"/api/experiments/{created['id']}/holdout", json={"trial": 1}).status_code == 200
        elif history == "analysis":  # outside any project, so not capped
            assert client.post("/api/analyses", json=document(bars_input(dataset)).model_dump(mode="json")).status_code == 200
        else:  # only the observations in scope were read, joined at other bars
            observations = import_observations(home, OBSERVATIONS_CSV, observations_declaration())
            elsewhere = import_csv(home, BARS_CSV, ImportDeclaration.model_validate(bars_declaration(name="elsewhere")))
            here = import_csv(home, BARS_CSV, ImportDeclaration.model_validate(bars_declaration()))
            joined = {"kind": "observations", "dataset": observations, "column": "Rate", "on": elsewhere}
            assert client.post("/api/analyses", json=document({"values": joined}).model_dump(mode="json")).status_code == 200
            scope, start_date = [observations, here], date(2024, 12, 2)
            tested = hypothesis({"values": joined | {"on": here}})
        project_id = new_project(client, scope, start_date)
        refused = client.post("/api/families", json=family_doc(project_id, [tested]))
        assert refused.status_code == 422 and "A confirmatory family cannot be registered: the test period" in refused.json()["detail"], refused.text
        post_hoc = client.post("/api/families", json=family_doc(project_id, [tested], confirmatory=False))
        assert post_hoc.status_code == 200 and not post_hoc.json()["confirmatory"]


def test_specifications_must_stay_in_scope_and_name_experiments_that_stop_before_the_test_period(home):
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        other = import_csv(home, BARS_CSV, ImportDeclaration.model_validate(bars_declaration()))
        project_id = new_project(client, [dataset], start_date)
        late = create(client, experiment(new_strategy(client, dataset), date(2025, 10, 1), [("fast", "period", [5, 10])]))
        refused = client.post(
            "/api/families",
            json=family_doc(project_id, [hypothesis(bars_input(other)), hypothesis({"values": {"kind": "trial", "experiment": late["id"]}}, "top")]),
        )
        detail = refused.json()["detail"]
        assert refused.status_code == 422 and f"mean: input 'values' reads datasets outside the project's scope: {other}." in detail
        assert "top: input 'values': the experiment's trials read to 2025-10-01, past the confirmation start." in detail
        run_input = client.post("/api/families", json=family_doc(project_id, [hypothesis({"values": {"kind": "equity", "run": "0" * 12}})]))
        assert "a hypothesis reads a run only through an experiment's trial" in run_input.json()["detail"]


def test_a_hypothesis_names_an_experiments_top_trial_before_it_is_known_and_is_evaluated_once(home):
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        project_id = new_project(client, [dataset], start_date)
        created = create(client, experiment(new_strategy(client), start_date, [("fast", "period", [5, 10, 15])]))
        top = hypothesis({"values": {"kind": "trial", "experiment": created["id"]}}, "top")
        family = client.post("/api/families", json=family_doc(project_id, [top, hypothesis(bars_input(dataset))])).json()
        summary = finished(client, created)
        evaluated = client.post(f"/api/families/{family['id']}/evaluate", json={"hypothesis": "top"})
        assert evaluated.status_code == 200, evaluated.text
        evaluation = evaluated.json()["evaluations"][0]
        best = summary["ranking"]["runs"][0]["trial"]
        holdout = client.get(f"/api/experiments/{created['id']}").json()["holdout"]
        assert [(h["trial"], h["run"]["id"]) for h in holdout] == [(best, evaluation["holdout_run"])]
        done = wait_analysis(client, evaluation["analysis"]["id"])
        assert done["status"] == "succeeded"
        shown = client.get(f"/api/analyses/{done['id']}/result").json()
        # The holdout's equity from the confirmation start: 100 sessions of returns, the first from the close before.
        assert shown["document"]["since"] == start_date.isoformat() and shown["result"]["values"]["n"] == 100
        again = client.post(f"/api/families/{family['id']}/evaluate", json={"hypothesis": "top"})
        assert again.status_code == 422 and "its first evaluation is binding" in again.json()["detail"]
        # The project's other confirmatory evaluation read the test period too, and does not compromise it.
        assert client.post(f"/api/families/{family['id']}/evaluate", json={"hypothesis": "mean"}).status_code == 200
        assert not client.get(f"/api/families/{family['id']}").json()["compromised"]


def test_an_unfinished_experiment_or_withdrawn_hypothesis_is_refused_without_reading(home):
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        project_id = new_project(client, [dataset], start_date)
        created = create(client, experiment(new_strategy(client), start_date, [("fast", "period", [5, 10])]))
        family = client.post(
            "/api/families", json=family_doc(project_id, [hypothesis({"values": {"kind": "trial", "experiment": created["id"]}}, "top")])
        ).json()
        early = client.post(f"/api/families/{family['id']}/evaluate", json={"hypothesis": "top"})
        assert early.status_code == 422 and "trials are ranked once every one has finished" in early.json()["detail"]
        assert client.get(f"/api/families/{family['id']}").json()["evaluations"] == []
        withdrawn = client.post(f"/api/families/{family['id']}/withdraw", json={"hypothesis": "top", "reason": "data error"})
        assert withdrawn.json()["withdrawals"][0]["reason"] == "data error"
        finished(client, created)
        refused = client.post(f"/api/families/{family['id']}/evaluate", json={"hypothesis": "top"})
        assert refused.status_code == 422 and "was withdrawn; it counts as p = 1" in refused.json()["detail"]
        assert client.post(f"/api/families/{family['id']}/withdraw", json={"hypothesis": "top", "reason": "again"}).status_code == 422


def test_a_read_of_the_test_period_after_registration_compromises_a_confirmatory_family(home):
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        project_id = new_project(client, [dataset], start_date)
        family = client.post("/api/families", json=family_doc(project_id, [hypothesis(bars_input(dataset))])).json()
        post_hoc = client.post("/api/families", json=family_doc(project_id, [hypothesis(bars_input(dataset))], confirmatory=False)).json()
        assert client.post(f"/api/families/{post_hoc['id']}/evaluate", json={"hypothesis": "mean"}).status_code == 200
        # A post hoc family's evaluation is not a confirmatory one: it compromises the confirmatory family.
        shown = client.get(f"/api/families/{family['id']}").json()
        assert shown["compromised"]
        refused = client.post(f"/api/families/{family['id']}/evaluate", json={"hypothesis": "mean"})
        assert refused.status_code == 422 and "The family is compromised" in refused.json()["detail"]
        # Registration is refused too now, so it cannot be redone.
        assert client.post("/api/families", json=family_doc(project_id, [hypothesis(bars_input(dataset))])).status_code == 422


def test_registrations_withdrawals_evaluations_and_reads_are_never_changed_or_removed(home):
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        project_id = new_project(client, [dataset], start_date)
        family = client.post("/api/families", json=family_doc(project_id, [hypothesis(bars_input(dataset))])).json()
        client.post(f"/api/projects/{project_id}/notes", json={"text": "first"})
        notes = client.post(f"/api/projects/{project_id}/notes", json={"text": "second"}).json()["notes"]
        assert [(n["revision"], n["text"]) for n in notes] == [(1, "first"), (2, "second")]
        client.post(f"/api/families/{family['id']}/withdraw", json={"hypothesis": "mean", "reason": "r"})
    db = sqlite3.connect(home / "qrs.sqlite")
    db.execute("INSERT INTO reads (dataset, until, kind, source, recorded_at) VALUES ('d', NULL, 'run', 's', 't')")
    db.execute("INSERT INTO evaluations VALUES ('f', 'h', NULL, NULL, 'e', 't')")
    for table in ("projects", "project_notes", "families", "withdrawals", "evaluations", "reads"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute(f"UPDATE {table} SET rowid = rowid")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            db.execute(f"DELETE FROM {table}")
    db.rollback()
    db.close()
    # Through the store, replacing a row is refused too: REPLACE deletes the row it replaces.
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        Store(home / "qrs.sqlite")._query("INSERT OR REPLACE INTO withdrawals VALUES (?, 'mean', 'another reason', 't')", family["id"])


def test_a_store_from_before_the_read_history_is_backfilled_failing_closed(home):
    """A Phase 4 store: one run kept, one whose directory was deleted, and a trial whose directory
    was deleted. The deleted run reads every dataset present in full; the trial, to its holdout."""
    with client_for(home) as client:
        dataset = synthetic(client)
        kept = wait(client, start(client, new_strategy(client))["id"])
        gone = wait(client, start(client, new_strategy(client))["id"])
        created = finished(client, create(client, experiment(new_strategy(client), holdout_start(home), [("fast", "period", [5])])))
    trial = created["trials"][0]["run"]["id"]
    for run_id in (gone["id"], trial):
        shutil.rmtree(home / "runs" / run_id)
    db = sqlite3.connect(home / "qrs.sqlite")
    db.execute("DROP TABLE reads")
    db.commit()
    db.close()
    later = import_csv(home, BARS_CSV, ImportDeclaration.model_validate(bars_declaration()))  # present at the backfill, unread by any run
    store = Store(home / "qrs.sqlite")
    reads = {(r["source"], r["dataset"]): (r["kind"], r["until"]) for r in store.reads([dataset, later])}
    assert reads[(kept["id"], dataset)] == ("run", None)
    assert reads[(gone["id"], dataset)] == ("unknown run", None) and reads[(gone["id"], later)] == ("unknown run", None)
    assert reads[(trial, dataset)] == ("unknown trial", holdout_start(home).isoformat())
    assert (kept["id"], later) not in reads  # its frozen strategy tells what it read
    # Data imported after the backfill is not touched by the unknown reads.
    fresh = import_csv(home, BARS_CSV.replace(b"105.50\n", b"105.75\n"), ImportDeclaration.model_validate(bars_declaration(name="fresh")))
    with client_for(home) as client:
        project_id = new_project(client, [fresh], date(2024, 12, 2))
        given = {"values": {"kind": "bars", "dataset": fresh}}
        assert client.post("/api/families", json=family_doc(project_id, [hypothesis(given)])).status_code == 200
        project_id = new_project(client, [later], date(2024, 12, 2))
        assert client.post("/api/families", json=family_doc(project_id, [hypothesis({"values": {"kind": "bars", "dataset": later}})])).status_code == 422


def test_cli_runs_analyses_and_exports_are_reads(home, tmp_path):
    dataset = datasets.list_manifests(home).popitem()[0]
    strategy = tmp_path / "s.json"
    with client_for(home) as client:
        strategy.write_text(__import__("json").dumps(new_strategy(client)["document"]))
    assert cli.main(["run", str(strategy), "--data", str(home), "--out", str(tmp_path / "run"), "--until", "2024-06-03"]) == 0
    assert cli.main(["export", str(strategy), str(tmp_path / "b.zip"), "--data", str(home)]) == 0
    (tmp_path / "a.json").write_text(document(bars_input(dataset), until="2024-03-01").model_dump_json())
    assert cli.main(["analyse", str(tmp_path / "a.json"), "--home", str(home), "--out", str(tmp_path / "a")]) == 0
    kinds = [(r["kind"], r["until"]) for r in Store(home / "qrs.sqlite").reads([dataset])]
    assert kinds == [("cli run", "2024-06-03"), ("export", None), ("cli analysis", "2024-03-01")]
    project = tmp_path / "p.json"
    project.write_text(__import__("json").dumps(project_doc([dataset], date(2025, 1, 2))))
    assert cli.main(["project", str(project), "--home", str(home)]) == 0
    project_id = Store(home / "qrs.sqlite").list_projects()[0]["id"]
    family = tmp_path / "f.json"
    family.write_text(__import__("json").dumps(family_doc(project_id, [hypothesis(bars_input(dataset))])))
    assert cli.main(["family", str(family), "--home", str(home)]) == 1  # the export read it in full


def test_reaching_the_test_period_is_a_cutoff_after_the_confirmation_start():
    project = ProjectDocument(name="p", question="q", datasets=["sha256:" + "0" * 64], confirmation_start=date(2025, 1, 2))
    assert not projects.reaches_test_period({"until": "2025-01-02"}, project)
    assert projects.reaches_test_period({"until": "2025-01-03"}, project) and projects.reaches_test_period({"until": None}, project)


# Regressions from the workstream's review.


def drop_reads(home):
    db = sqlite3.connect(home / "qrs.sqlite")
    db.execute("DROP TABLE reads")
    db.commit()
    db.close()


def test_a_backfill_that_fails_leaves_no_history_and_an_unreadable_run_is_an_unknown_read(home, monkeypatch):
    with client_for(home) as client:
        dataset = synthetic(client)
        broken = wait(client, start(client, new_strategy(client))["id"])
    (home / "runs" / broken["id"] / "strategy.json").chmod(0o644)
    (home / "runs" / broken["id"] / "strategy.json").write_text("{not json")
    drop_reads(home)
    monkeypatch.setattr(Store, "_backfilled_reads", lambda self: (_ for _ in ()).throw(RuntimeError("crashed")))
    with pytest.raises(RuntimeError, match="crashed"):
        Store(home / "qrs.sqlite")
    monkeypatch.undo()
    reads = Store(home / "qrs.sqlite").reads([dataset])  # the next open backfills again
    assert [(r["kind"], r["source"], r["until"]) for r in reads] == [("unknown run", broken["id"], None)]


def test_a_read_recorded_while_a_family_registers_compromises_it(home, monkeypatch):
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        project_id = new_project(client, [dataset], start_date)
        store = client.app.state.jobs.store
        checked = projects.test_period_reads
        calls = []

        def with_a_read_meanwhile(*args, **kwargs):
            found = checked(*args, **kwargs)
            if not calls:  # once, during registration's check; the response's status calls it again
                store.record_reads([dataset], None, "run", "elsewhere")  # another process's run, between check and record
            calls.append(1)
            return found

        monkeypatch.setattr(projects, "test_period_reads", with_a_read_meanwhile)
        family = client.post("/api/families", json=family_doc(project_id, [hypothesis(bars_input(dataset))])).json()
        monkeypatch.setattr(projects, "test_period_reads", checked)
        assert client.get(f"/api/families/{family['id']}").json()["compromised"]


def test_a_hypothesis_evaluated_twice_at_once_is_evaluated_once(home):
    import threading

    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        project_id = new_project(client, [dataset], start_date)
        family = client.post("/api/families", json=family_doc(project_id, [hypothesis(bars_input(dataset))])).json()
        jobs = client.app.state.jobs
        outcomes = []

        def evaluate():
            try:
                outcomes.append(projects.evaluate(jobs.store, jobs, family["id"], "mean")["id"])
            except projects.ProjectError as e:
                outcomes.append(str(e))

        threads = [threading.Thread(target=evaluate) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum("its first evaluation is binding" in o for o in outcomes) == 1
        assert len(jobs.store.project_analyses(project_id)) == 1


def test_a_copy_of_a_dataset_under_another_id_and_features_of_the_same_documents_share_its_reads(home):
    store = Store(home / "qrs.sqlite")
    original = import_csv(home, BARS_CSV, ImportDeclaration.model_validate(bars_declaration()))
    copy = import_csv(home, BARS_CSV, ImportDeclaration.model_validate(bars_declaration(name="the same file, renamed")))
    assert original != copy and copy in projects.related(store, [original])
    from qrs import templates

    chosen = templates.prepare("macro_ridge", home)
    features = chosen[datasets.OBSERVATIONS]
    documents = datasets.read_manifest(home, features).derivation.documents
    assert {features, documents} <= set(projects.related(store, [documents])) and documents in projects.related(store, [features])
    store.record_reads([copy], None, "analysis", "a")
    project = ProjectDocument(name="p", question="q", datasets=[original], confirmation_start=date(2024, 12, 2))
    assert [r["dataset"] for r in projects.test_period_reads(store, project)] == [copy]


def test_extraction_and_exports_are_reads_recorded_before_they_happen(home, tmp_path, monkeypatch):
    from qrs import templates

    chosen = templates.prepare("macro_ridge", tmp_path / "data")
    spec = templates.load("macro_ridge").fixture.extraction
    (tmp_path / "extraction.json").write_text(spec.model_dump_json())
    assert cli.main(["extract", str(tmp_path / "extraction.json"), "--home", str(tmp_path / "data")]) == 0
    reads = Store(tmp_path / "data" / "qrs.sqlite").reads([spec.documents])
    # The command's own read; the fixture's earlier extraction is backfilled as one too.
    assert [(r["kind"], r["until"]) for r in reads if r["source"] == spec.name] == [("extract", None)]
    strategy = tmp_path / "s.json"
    strategy.write_text(templates.instantiate("macro_ridge", chosen=chosen).model_dump_json())
    monkeypatch.setattr("qrs.export.build", lambda *a: (_ for _ in ()).throw(RuntimeError("failed")))
    with pytest.raises(RuntimeError):
        cli.main(["export", str(strategy), str(tmp_path / "b.zip"), "--data", str(tmp_path / "data")])
    assert ("export", None) in [(r["kind"], r["until"]) for r in Store(tmp_path / "data" / "qrs.sqlite").reads([chosen[datasets.BARS]])]


def test_an_evaluation_that_fails_after_its_holdout_is_queued_is_bound_as_p_1(home, monkeypatch):
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        project_id = new_project(client, [dataset], start_date)
        created = finished(client, create(client, experiment(new_strategy(client), start_date, [("fast", "period", [5, 10])])))
        family = client.post(
            "/api/families", json=family_doc(project_id, [hypothesis({"values": {"kind": "trial", "experiment": created["id"]}}, "top")])
        ).json()
        jobs = client.app.state.jobs
        monkeypatch.setattr(jobs, "submit_analysis", lambda *a, **k: (_ for _ in ()).throw(ValueError("the analysis could not be queued")))
        failed = client.post(f"/api/families/{family['id']}/evaluate", json={"hypothesis": "top"})
        assert failed.status_code == 422 and "counts as p = 1" in failed.json()["detail"]
        monkeypatch.undo()
        shown = client.get(f"/api/families/{family['id']}").json()
        assert shown["evaluations"][0]["holdout_run"] and shown["evaluations"][0]["error"] == "the analysis could not be queued"
        assert client.post(f"/api/families/{family['id']}/evaluate", json={"hypothesis": "top"}).status_code == 422
        # The holdout of a family's evaluation is never rerun outside it.
        assert client.post(f"/api/runs/{shown['evaluations'][0]['holdout_run']}/rerun").status_code == 409
        reads = jobs.store.reads([dataset])
        assert [(r["kind"], r["family"]) for r in reads if r["source"] == shown["evaluations"][0]["holdout_run"]] == [("holdout", family["id"])]


def test_another_projects_confirmatory_evaluation_compromises_a_family(home):
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        mine, theirs = new_project(client, [dataset], start_date), new_project(client, [dataset], start_date)
        family = client.post("/api/families", json=family_doc(mine, [hypothesis(bars_input(dataset))])).json()
        other = client.post("/api/families", json=family_doc(theirs, [hypothesis(bars_input(dataset))])).json()
        assert client.post(f"/api/families/{other['id']}/evaluate", json={"hypothesis": "mean"}).status_code == 200
        assert client.get(f"/api/families/{family['id']}").json()["compromised"]


def test_since_leaves_earlier_events_and_observations_out_of_the_sample(tmp_path):
    from qrs import analysis
    from test_observations import BARS_CSV as CSV

    bars = import_csv(tmp_path, CSV, ImportDeclaration.model_validate(bars_declaration()))
    observations = import_observations(tmp_path, OBSERVATIONS_CSV, observations_declaration())
    given = {"kind": "observations", "dataset": observations, "column": "Rate", "on": bars}
    context = analysis.Context(document({"returns": {"kind": "bars", "dataset": bars}, "events": given}, since="2024-12-02"), tmp_path)
    assert context.events("events").strftime("%Y-%m-%d").tolist() == ["2024-12-02", "2024-12-03"]
    assert context.series("returns").index[0] == context.series("events").index[0] == pd.Timestamp("2024-12-02")
    assert (context.rows("events")[1]["published"] >= pd.Timestamp("2024-12-02", tz="UTC")).all()


def test_a_read_after_every_hypothesis_is_settled_leaves_the_family_as_it_concluded(home):
    """A family is compromised only by reads while a hypothesis was open: a study's later use of the
    data cannot relabel results that were already fixed, while a family still open is compromised."""
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        project_id = new_project(client, [dataset], start_date)
        finished_family = client.post("/api/families", json=family_doc(project_id, [hypothesis(bars_input(dataset))])).json()
        two = [hypothesis(bars_input(dataset)), hypothesis(bars_input(dataset), id="later")]
        open_family = client.post("/api/families", json=family_doc(project_id, two)).json()
        assert client.post(f"/api/families/{finished_family['id']}/evaluate", json={"hypothesis": "mean"}).status_code == 200
        wait(client, start(client, new_strategy(client))["id"])  # a full read of the test period
        assert not client.get(f"/api/families/{finished_family['id']}").json()["compromised"]
        assert client.get(f"/api/families/{open_family['id']}").json()["compromised"]
        # Settling a family afterwards does not clear a read made while it was open.
        for hypothesis_id in ("mean", "later"):
            client.post(f"/api/families/{open_family['id']}/withdraw", json={"hypothesis": hypothesis_id, "reason": "r"})
        assert client.get(f"/api/families/{open_family['id']}").json()["compromised"]


def test_a_store_from_before_the_read_history_counts_each_extraction_as_a_full_read_of_its_documents(home):
    from qrs import templates

    features = templates.prepare("macro_ridge", home)[datasets.OBSERVATIONS]
    documents = datasets.read_manifest(home, features).derivation.documents
    Store(home / "qrs.sqlite")
    drop_reads(home)
    reads = Store(home / "qrs.sqlite").reads([documents])
    assert [(r["dataset"], r["until"], r["kind"], r["source"]) for r in reads] == [(documents, None, "extract", features)]


def test_an_evaluated_hypothesis_cannot_be_withdrawn(home):
    """Its first evaluation is binding: withdrawing it afterwards would hide the result as p = 1."""
    with client_for(home) as client:
        dataset, start_date = synthetic(client), holdout_start(home)
        project_id = new_project(client, [dataset], start_date)
        family = client.post("/api/families", json=family_doc(project_id, [hypothesis(bars_input(dataset))])).json()
        evaluation = client.post(f"/api/families/{family['id']}/evaluate", json={"hypothesis": "mean"}).json()["evaluations"][0]
        wait_analysis(client, evaluation["analysis"]["id"])
        refused = client.post(f"/api/families/{family['id']}/withdraw", json={"hypothesis": "mean", "reason": "r"})
        assert refused.status_code == 422 and "cannot be withdrawn" in refused.json()["detail"]
        assert client.get(f"/api/families/{family['id']}").json()["outcomes"][0]["state"] == "evaluated"


def test_features_of_documents_reimported_under_another_name_share_their_reads(home):
    """Lineage is followed to a fixed point: the same documents file imported twice, and the features
    extracted from each copy, are all one data."""
    from qrs import extraction, importer, templates

    fixture = templates.load("macro_ridge").fixture
    first_features = templates.prepare_features("macro_ridge", home)
    first = fixture.extraction.documents
    content = (templates.TEMPLATE_DIR / "macro_ridge.documents.csv").read_bytes()
    second = importer.import_documents(home, content, fixture.documents.model_copy(update={"name": "the same file again"}))
    second_features = extraction.extract(home, fixture.extraction.model_copy(update={"documents": second, "name": "again"}), report=lambda _: None)
    store = Store(home / "qrs.sqlite")
    assert len({first, second, first_features, second_features}) == 4
    assert {second, second_features} <= set(projects.related(store, [first_features]))
    assert {second, second_features} <= set(projects.related(store, [first]))

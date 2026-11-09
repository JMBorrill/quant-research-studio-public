"""Stage 5 workstream 10: the corn research programme runs end to end offline on its synthetic
fixtures (import, extraction, project, exploration, experiment, families, evaluation, report and
verify); continuous futures keep their roll, adjustment and contract provenance and are never
traded (amendment A3); and no programme term appears in the core."""

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from pydantic import ValidationError

from conftest import client_for
from qrs import analysis, datasets, importer, templates
from qrs.compiler import InvalidStrategy, compile_for_data
from qrs.contracts import AnalysisDocument, Edge, ImportDeclaration, Node, PortRef
from test_analysis import run_with_record, wait_analysis

PROGRAMME = Path(__file__).parents[1] / "research" / "corn"
sys.path.insert(0, str(PROGRAMME))
import run as programme  # noqa: E402

# Whole words, and parts of identifiers such as crop_progress: not letters on either side.
TERMS = re.compile(r"(?<![a-z])(corn|wasde|usda|nass|cftc|crop)(?![a-z])", re.IGNORECASE)


def test_no_programme_term_appears_in_the_core():
    core = Path(__file__).parents[1] / "src" / "qrs"
    found = [
        f"{p.relative_to(core)}:{n}"
        for p in core.rglob("*")
        if p.is_file() and p.suffix in (".py", ".json", ".csv", ".jsonl", ".lock")
        for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1)
        if TERMS.search(line)
    ]
    assert found == []


def test_the_corn_programme_runs_end_to_end_offline(tmp_path):
    with client_for(tmp_path) as client:
        found = programme.run(client, tmp_path, PROGRAMME / "fixtures")
        ids, report = found["ids"], found["report"]
        assert found["verified"] == {"verified": True, "problems": []}
        evidence = report["evidence"]
        market, strategy = (evidence["families"][ids[f"family:{f}"]] for f in ("market", "strategy"))
        # Both families confirmatory and complete: every hypothesis evaluated, its p-value adjusted.
        for family in (market, strategy):
            assert family["confirmatory"] and not family["compromised"] and family["complete"], family
            assert all(o["state"] == "evaluated" and o["adjusted_p_value"] is not None for o in family["outcomes"]), family["outcomes"]
        assert [o["hypothesis"] for o in market["outcomes"]] == ["months", "reports", "condition", "positioning"]
        # The calendar-month test has every month of the three test years: F on (11, 2) degrees of
        # freedom; positioning is ranked over its whole history, so its sample is the whole test period.
        values = {
            e["hypothesis"]: client.get(f"/api/analyses/{e['analysis']['id']}/result").json()["result"]["values"]
            for e in client.get(f"/api/families/{ids['family:market']}").json()["evaluations"]
        }
        assert values["months"]["df"] == [11, 2] and values["months"]["years"] == 3
        assert values["positioning"]["n"] == values["reports"]["n"]  # the label at the close before the first test session is known
        # The fixtures are synthetic, the weakest class, so every finding is synthetic; on real data the
        # tone, read by a model, would be exploratory (test_ai_derived_inputs_make_findings_exploratory).
        assert evidence["analyses"][ids["analysis:report_tone"]]["evidence_class"] == "synthetic" and evidence["evidence_class"] == "synthetic"
        assert evidence["redistributable"] and evidence["horizons"][ids["continuous"]]["test_period_read"]
        # The selection is disclosed: N in the deflated Sharpe ratio and the Reality Check.
        deflated = json.loads(client.get(f"/api/analyses/{ids['analysis:search_deflated']}/result").text)["result"]["values"]
        assert deflated["trials"] == 6 and 0 <= deflated["deflated_sharpe"] <= 1
        # Exploration stopped at the confirmation start; the continuous series' rolls are reported.
        seasonal = client.get(f"/api/analyses/{ids['analysis:seasonal_profile']}/result").json()
        assert seasonal["result"]["cutoff"] == "2022-01-03" and len(seasonal["record"]["inputs"]["returns"]["rolls"]) == 5 * 16
        html = client.get(f"/api/reports/{report['id']}/html").text
        assert "Corn futures research programme" in html and "<script" not in html


def declared(name: str, **changes) -> dict:
    declaration = json.loads((PROGRAMME / "declarations" / f"{name}.json").read_text())
    return declaration | changes


def import_continuous(home, declaration: dict) -> str:
    return importer.import_csv(home, (PROGRAMME / "fixtures" / "continuous.csv").read_bytes(), ImportDeclaration.model_validate(declaration))


def test_a_continuous_series_keeps_complete_provenance_and_is_retrospective_at_best(tmp_path):
    good = declared("continuous")
    dataset_id = import_continuous(tmp_path, good)
    manifest = datasets.read_manifest(tmp_path, dataset_id)
    series = manifest.instrument.continuous
    assert manifest.instrument.expiry is None and series.adjustment == "difference" and len(series.contracts) == 96
    assert series.contracts[0].symbol == "ZCH06" and series.contracts[-1].adjustment == 0
    contracts = good["instrument"]["continuous"]["contracts"]
    gap = [c for i, c in enumerate(contracts) if i != 10]  # a contract left out: its sessions belong to none
    # A roll declared on a day between two sessions covers the same sessions but misstates the roll session.
    i = next(
        i
        for i in range(1, len(contracts))
        if date.fromisoformat(contracts[i]["first_session"]) - timedelta(days=1) > date.fromisoformat(contracts[i - 1]["last_session"])
    )
    early = [c | {"first_session": (date.fromisoformat(c["first_session"]) - timedelta(days=1)).isoformat()} if j == i else c for j, c in enumerate(contracts)]
    for changes, message in (
        ({"instrument": good["instrument"] | {"continuous": good["instrument"]["continuous"] | {"contracts": gap}}}, "belong to no contract"),
        ({"instrument": good["instrument"] | {"continuous": good["instrument"]["continuous"] | {"contracts": early}}}, "start and end on sessions"),
        ({"evidence_class": "point_in_time"}, "retrospective at best"),
    ):
        with pytest.raises(datasets.DatasetError, match=message):
            import_continuous(tmp_path, declared("continuous", **changes))
    late = [*contracts[:-1], contracts[-1] | {"last_session": contracts[-1]["expiry"]}]
    spliced = good["instrument"]["continuous"] | {"adjustment": "none"}
    for continuous, message in ((good["instrument"]["continuous"] | {"contracts": late}, "before its expiry"), (spliced, "cannot adjust")):
        with pytest.raises(ValidationError, match=message):
            ImportDeclaration.model_validate(declared("continuous", instrument=good["instrument"] | {"continuous": continuous}))
    with pytest.raises(ValidationError, match="has an expiry, or is a continuous series"):
        ImportDeclaration.model_validate(declared("continuous", instrument=good["instrument"] | {"expiry": "2030-01-01"}))


def test_a_continuous_series_is_never_traded_and_is_read_as_price_changes(tmp_path):
    dataset_id = import_continuous(tmp_path, declared("continuous"))
    with pytest.raises(InvalidStrategy, match="cannot trade a continuous series"):
        compile_for_data(templates.instantiate("ema_crossover", dataset_id), tmp_path)
    refused = AnalysisDocument(name="p", method="qrs.summary", method_version=1, inputs={"values": {"kind": "bars", "dataset": dataset_id}})
    with pytest.raises(analysis.AnalysisError, match="read price_change"):
        analysis.check(refused, tmp_path, lambda _: None)
    spliced = declared("continuous")
    segments = [c | {"adjustment": 0.0} for c in spliced["instrument"]["continuous"]["contracts"]]
    spliced["instrument"]["continuous"] |= {"adjustment": "none", "contracts": segments}
    unadjusted = import_continuous(tmp_path, spliced)
    gaps = AnalysisDocument.model_validate(refused.model_dump() | {"inputs": {"values": {"kind": "bars", "dataset": unadjusted, "measure": "price_change"}}})
    with pytest.raises(analysis.AnalysisError, match="include every roll gap"):
        analysis.check(gaps, tmp_path, lambda _: None)
    # Nor through a run's record of a spliced series' levels, which would carry the roll gaps unseen;
    # a difference-adjusted one may be recorded beside the traded contract (A3).
    contract = importer.import_csv(tmp_path, (PROGRAMME / "fixtures" / "zcz24.csv").read_bytes(), ImportDeclaration.model_validate(declared("zcz24")))
    doc = templates.instantiate("ema_crossover", contract)
    doc.nodes += [
        Node(id="continuous", operator="qrs.dataset", version=1, params={"dataset_id": unadjusted}),
        Node(id="level", operator="qrs.column", version=1, params={"column": "close"}),
        Node(id="table", operator="qrs.table", version=1, params={"name": "level"}),
        Node(id="kept", operator="qrs.record", version=1),
    ]
    wires = (("continuous", "bars", "level", "bars"), ("level", "series", "table", "series"), ("table", "table", "kept", "table"))
    doc.edges += [Edge(source=PortRef(node=a, port=b), target=PortRef(node=c, port=d)) for a, b, c, d in wires]
    run_id = run_with_record(tmp_path, None, doc)
    recorded = AnalysisDocument.model_validate(
        refused.model_dump() | {"inputs": {"values": {"kind": "record", "run": run_id, "node": "kept", "column": "level"}}}
    )
    with pytest.raises(analysis.AnalysisError, match="spliced without adjustment, so its values include every roll gap"):
        analysis.check(recorded, tmp_path, lambda _: "succeeded")
    doc.nodes = [n.model_copy(update={"params": {"dataset_id": dataset_id}}) if n.id == "continuous" else n for n in doc.nodes]
    adjusted = run_with_record(tmp_path, date(2024, 10, 1), doc)
    analysis.check(
        AnalysisDocument.model_validate(recorded.model_dump() | {"inputs": {"values": {"kind": "record", "run": adjusted, "node": "kept", "column": "level"}}}),
        tmp_path,
        lambda _: "succeeded",
    )


def test_instruments_that_are_not_continuous_keep_their_manifests_and_ids():
    assert "continuous" not in datasets.SYNTHETIC_INSTRUMENT.model_dump(mode="json", exclude_none=True)
    # The Stage 2 demo's dataset ID, which the historical hashes also pin.
    with TemporaryDirectory() as home:
        assert datasets.create_synthetic(Path(home)) == "sha256:91a9b5201e1f05417d89662020f3b5760a195d26518b8030daba8af04e7c9248"


def test_ai_derived_inputs_make_findings_exploratory(home):
    from test_exploratory import news_with_retrospective_data

    chosen = news_with_retrospective_data(home)
    with client_for(home) as client:
        given = {"kind": "observations", "dataset": chosen[datasets.OBSERVATIONS], "column": "sentiment", "on": chosen[datasets.BARS]}
        doc = AnalysisDocument(name="tone", method="qrs.summary", method_version=1, inputs={"values": given})
        queued = client.post("/api/analyses", json=doc.model_dump(mode="json")).json()
        assert wait_analysis(client, queued["id"])["status"] == "succeeded"
        assert client.get(f"/api/analyses/{queued['id']}/result").json()["result"]["evidence_class"] == "exploratory"


def test_provenance_must_agree_with_itself_and_the_prices(tmp_path):
    good = declared("continuous")
    contracts = good["instrument"]["continuous"]["contracts"]

    def with_contracts(changed: list[dict], **series) -> dict:
        return declared("continuous", instrument=good["instrument"] | {"continuous": good["instrument"]["continuous"] | {"contracts": changed} | series})

    overlapping = [*contracts[:5], contracts[5] | {"first_session": contracts[4]["first_session"]}, *contracts[6:]]
    shorter = contracts[:-1]
    for declaration, message in ((with_contracts(shorter), "the contracts cover"), (with_contracts(overlapping), "not after")):
        with pytest.raises((datasets.DatasetError, ValidationError), match=message):
            import_continuous(tmp_path, declaration)
    negative = [c | {"adjustment": c["adjustment"] + 10.0} for c in contracts]  # $10 a bushel above every price
    with pytest.raises(datasets.DatasetError, match="priced at zero or less"):
        import_continuous(tmp_path, with_contracts(negative))
    for changed, message in (
        ([contracts[0] | {"adjustment": 0.001234}, *contracts[1:]], "multiples of the tick size"),
        ([contracts[0], contracts[0] | {"first_session": contracts[1]["first_session"]}, *contracts[2:]], "appears once"),
        (
            [
                contracts[1] | {"first_session": contracts[0]["first_session"], "last_session": contracts[0]["last_session"]},
                contracts[0] | {"first_session": contracts[1]["first_session"], "last_session": contracts[1]["last_session"]},
                *contracts[2:],
            ],
            "expires on",
        ),
        ([c | {"adjustment": 0.0} for c in contracts], "with none it is spliced"),
    ):
        with pytest.raises(ValidationError, match=message):
            ImportDeclaration.model_validate(with_contracts(changed))


def test_percentages_of_a_continuous_series_cannot_be_recorded(tmp_path):
    from conftest import run_recorded
    from qrs import compiler, graph
    from qrs.contracts import Edge, Node, PortRef

    continuous = import_continuous(tmp_path, declared("continuous"))
    contract = importer.import_csv(tmp_path, (PROGRAMME / "fixtures" / "zcz24.csv").read_bytes(), ImportDeclaration.model_validate(declared("zcz24")))
    doc = templates.instantiate("ema_crossover", contract)
    doc.nodes += [Node(id="other", operator="qrs.dataset", version=1, params={"dataset_id": continuous}), Node(id="pct", operator="qrs.returns", version=1)]
    doc.nodes += [Node(id="table", operator="qrs.table", version=1), Node(id="kept", operator="qrs.record", version=1)]
    wires = [("other", "bars", "pct", "bars"), ("pct", "series", "table", "series"), ("table", "table", "kept", "table")]
    doc.edges += [Edge(source=PortRef(node=a, port=b), target=PortRef(node=c, port=d)) for a, b, c, d in wires]
    assert graph.validate(doc) == []
    strategy = tmp_path / "s.py"
    strategy.write_text(compiler.compile_for_data(doc, tmp_path))  # trading the contract, so it compiles
    with pytest.raises(Exception, match="no meaningful percentage returns"):
        run_recorded(strategy, tmp_path)


def test_neither_percentage_operator_reads_a_continuous_series(tmp_path):
    from qrs.operators import features, indicators

    bars = datasets.load(tmp_path, import_continuous(tmp_path, declared("continuous")))
    for operator, params in ((indicators.returns, indicators.ReturnsParams()), (features.forward_return, features.ForwardReturnParams())):
        with pytest.raises(ValueError, match="no meaningful percentage returns"):
            operator(None, {"bars": bars}, params)

"""The Stage 5 mutation pass: each safeguard the research layer adds is broken in turn, in a scratch
copy of the committed tree, and the tests that guard it must then fail. A mutation that survives
means a rule no test holds. Used by the local gate.

    uv run python scripts/check-mutations.py"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# (what it breaks, file, text, replacement, tests that must fail)
MUTATIONS = [
    (
        "vectorised as-of: the earliest of tied revisions stands",
        "src/qrs/operators/data.py",
        "-np.arange(len(rows))",
        "np.arange(len(rows))",
        "tests/test_record.py",
    ),
    (
        "one calendar across an analysis's inputs",
        "src/qrs/analysis.py",
        "    if len(keys) > 1:",
        "    if False:",
        "tests/test_describe.py::test_inputs_on_different_exchange_calendars_are_refused",
    ),
    (
        "a project caps its analyses at the confirmation start",
        "src/qrs/analysis.py",
        "    if doc.project is None:\n        return doc",
        "    if True:\n        return doc",
        "tests/test_projects.py::test_a_project_caps_its_analyses_and_a_clean_confirmatory_family_registers",
    ),
    (
        "confirmatory registration fails closed",
        "src/qrs/projects.py",
        "    if doc.confirmatory:\n        reads",
        "    if False:\n        reads",
        "tests/test_projects.py::test_confirmatory_registration_is_refused_once_the_test_period_was_read",
    ),
    (
        "reads after registration compromise a confirmatory family",
        "src/qrs/projects.py",
        'return [r for r in reads if r["family"] not in allowed and (settled is None or r["recorded_at"] <= settled)]',
        "return []",
        "tests/test_projects.py::test_a_read_of_the_test_period_after_registration_compromises_a_confirmatory_family",
    ),
    (
        "reads once a family is settled leave it as it concluded",
        "src/qrs/projects.py",
        'r["recorded_at"] <= settled',
        "True",
        "tests/test_projects.py::test_a_read_after_every_hypothesis_is_settled_leaves_the_family_as_it_concluded",
    ),
    (
        "settling a family does not clear reads made while it was open",
        "src/qrs/projects.py",
        '(settled is None or r["recorded_at"] <= settled)',
        "settled is None",
        "tests/test_projects.py::test_a_read_after_every_hypothesis_is_settled_leaves_the_family_as_it_concluded",
    ),
    (
        "a store from before the read history counts its extractions",
        "src/qrs/store.py",
        '                add([derivation.documents], None, "extract", dataset_id)',
        "                pass",
        "tests/test_projects.py::test_a_store_from_before_the_read_history_counts_each_extraction_as_a_full_read_of_its_documents",
    ),
    (
        "a run queued with an analysis goes first",
        "src/qrs/store.py",
        "ORDER BY created_at, kind DESC, inserted",
        "ORDER BY created_at, inserted, kind",
        "tests/test_analysis.py::test_a_run_queued_in_the_same_millisecond_as_an_analysis_goes_first",
    ),
    (
        "a re-run of a trial shows N",
        "src/qrs/analysis.py",
        '        run_id = row.get("predecessor")',
        "        run_id = None",
        "tests/test_selection.py::test_any_analysis_of_a_trial_or_its_holdout_shows_n",
    ),
    (
        "each hypothesis is evaluated once",
        "src/qrs/projects.py",
        '    if any(e["hypothesis"] == hypothesis_id for e in store.evaluations(family_id)):',
        "    if False:",
        "tests/test_projects.py::test_a_hypothesis_names_an_experiments_top_trial_before_it_is_known_and_is_evaluated_once",
    ),
    (
        "runs are reads, recorded before they are queued",
        "src/qrs/jobs.py",
        '        self.store.record_reads(graph.dataset_ids(doc), until, read or ("trial" if trial else "run"), run_id, family)',
        "        pass",
        "tests/test_projects.py::test_confirmatory_registration_is_refused_once_the_test_period_was_read",
    ),
    (
        "an unknown read in an older store counts as a full read",
        "src/qrs/store.py",
        '                add(present, until, f"unknown {kind}", run["id"])',
        "                pass",
        "tests/test_projects.py::test_a_store_from_before_the_read_history_is_backfilled_failing_closed",
    ),
    (
        "registrations and reads are append-only",
        "src/qrs/store.py",
        "        for table in APPEND_ONLY:",
        "        for table in ():",
        "tests/test_projects.py::test_registrations_withdrawals_evaluations_and_reads_are_never_changed_or_removed",
    ),
    (
        "copies of the same data share their reads",
        "src/qrs/projects.py",
        "        if not more:\n",
        "        if True:\n",
        "tests/test_projects.py::test_a_copy_of_a_dataset_under_another_id_and_features_of_the_same_documents_share_its_reads",
    ),
    (
        "lineage is followed to a fixed point",
        "src/qrs/projects.py",
        "        found |= more\n",
        "        return sorted(found | more)\n",
        "tests/test_projects.py::test_features_of_documents_reimported_under_another_name_share_their_reads",
    ),
    (
        "an evaluated hypothesis cannot be withdrawn",
        "src/qrs/projects.py",
        '        if any(e["hypothesis"] == hypothesis for e in store.evaluations(family_id)):\n            raise',
        "        if False:\n            raise",
        "tests/test_projects.py::test_an_evaluated_hypothesis_cannot_be_withdrawn",
    ),
    (
        "replacing a row meets the append-only triggers",
        "src/qrs/store.py",
        '        self._db.execute("PRAGMA recursive_triggers = ON")\n',
        "",
        "tests/test_projects.py::test_registrations_withdrawals_evaluations_and_reads_are_never_changed_or_removed",
    ),
    (
        "verify compares the report's bytes",
        "src/qrs/reports.py",
        'html.encode() != (report_dir / "report.html").read_bytes()',
        'html != (report_dir / "report.html").read_text()',
        "tests/test_reports.py::test_a_report_whose_text_holds_a_carriage_return_verifies",
    ),
    (
        "a continuous series' contracts start and end on sessions",
        "src/qrs/datasets.py",
        "    if off:\n",
        "    if False:\n",
        "tests/test_corn.py::test_a_continuous_series_keeps_complete_provenance_and_is_retrospective_at_best",
    ),
    (
        "a record fed by a spliced continuous series is not read",
        "src/qrs/analysis.py",
        " or _continuous_upstream(home, given)",
        "",
        "tests/test_corn.py::test_a_continuous_series_is_never_traded_and_is_read_as_price_changes",
    ),
    (
        "resampling beyond the plan's budget is refused",
        "src/qrs/analysis.py",
        "    if work <= budget:\n",
        "    if True:\n",
        "tests/test_analysis.py::test_resampling_beyond_the_plans_budget_is_refused_before_queuing",
    ),
    (
        "the regression's lag is h − 1 only where horizons overlap",
        "src/qrs/methods/inference.py",
        "params.horizon if overlapping else 1)",
        "params.horizon)",
        "tests/test_inference.py::test_changes_at_least_a_horizon_apart_take_the_plans_lag_and_never_span_a_gap",
    ),
    (
        "Holm multiplies by the hypotheses left",
        "src/qrs/stats.py",
        "min(1.0, (m - i) * p_values[index])",
        "min(1.0, p_values[index])",
        "tests/test_inference.py::test_adjustments_match_statsmodels_and_the_benjamini_hochberg_worked_example",
    ),
    (
        "withdrawn hypotheses count as p = 1",
        "src/qrs/projects.py",
        'state, p_value, estimate = "withdrawn", 1.0, None',
        'state, p_value, estimate = "withdrawn", 0.0, None',
        "tests/test_inference.py::test_a_familys_conclusions_follow_its_adjusted_p_values",
    ),
    (
        "p-values are one-sided in the registered direction",
        "src/qrs/stats.py",
        "    return p_two_sided / 2 if agrees else 1 - p_two_sided / 2",
        "    return p_two_sided / 2",
        "tests/test_inference.py::test_a_familys_conclusions_follow_its_adjusted_p_values",
    ),
    (
        "the seasonal Wald test is F on years less one",
        "src/qrs/methods/inference.py",
        ", df_denominator=int(counts.min()) - 1)",
        ")",
        "tests/test_inference.py::test_event_test_and_seasonal_test_detect_planted_effects_and_not_absent_ones",
    ),
    (
        "HAC errors, not i.i.d.",
        "src/qrs/stats.py",
        "        s += (1 - lag_l / (lag + 1)) * (gamma + gamma.T)",
        "        pass",
        "tests/test_inference.py::test_hac_regression_and_wald_match_statsmodels",
    ),
    (
        "N counts every trial on the window, cost sweeps excepted",
        "src/qrs/analysis.py",
        'return sum(e["trials"] for e in self.window if not e["costs_only"])',
        'return sum(e["trials"] for e in self.window)',
        "tests/test_selection.py::test_selection_methods_count_every_trial_on_the_window_and_list_failed_ones",
    ),
    (
        "an experiment is read only to its holdout start",
        "src/qrs/analysis.py",
        "    if until is not None and until < holdout:",
        "    if False:",
        "tests/test_selection.py::test_an_experiment_is_read_only_to_its_holdout_start_and_a_cost_sweep_deflates_nothing",
    ),
    (
        "regime labels are lagged in the returns' sessions",
        "src/qrs/methods/robustness.py",
        "label.reindex(returns.index).shift(1)",
        "label.shift(1).reindex(returns.index)",
        "tests/test_robustness.py::test_a_label_with_sessions_the_returns_lack_is_lagged_in_the_returns_sessions",
    ),
    (
        "reports check every artefact against its record",
        "src/qrs/reports.py",
        '    problem = runtime.verify_outputs(folder)\n    if problem:\n        raise ReportError(f"Analysis',
        '    problem = None\n    if problem:\n        raise ReportError(f"Analysis',
        "tests/test_reports.py::test_verify_is_stable_as_the_store_grows_and_finds_any_changed_artefact",
    ),
    (
        "report text is escaped",
        "src/qrs/reports.py",
        'return [f"<p>{escape(paragraph.strip())}</p>"',
        'return [f"<p>{paragraph.strip()}</p>"',
        "tests/test_reports.py::test_a_report_is_self_contained_escaped_and_traces_every_number",
    ),
    (
        "a backtest refuses a continuous series",
        "src/qrs/graph.py",
        '        if "backtest" in op.outputs.values() and continuous:',
        "        if False:",
        "tests/test_corn.py::test_a_continuous_series_is_never_traded_and_is_read_as_price_changes",
    ),
    (
        "a continuous series' contracts cover every session once",
        "src/qrs/datasets.py",
        "        problems += continuous_problems(index, instrument.continuous, frame)",
        "        pass",
        "tests/test_corn.py::test_a_continuous_series_keeps_complete_provenance_and_is_retrospective_at_best",
    ),
    (
        "strategy runs never import SciPy",
        "src/qrs/runtime.py",
        "from qrs import __version__, datasets, registry",
        "import scipy.stats  # noqa: F401\nfrom qrs import __version__, datasets, registry",
        "tests/test_analysis.py::test_strategy_execution_never_imports_scipy",
    ),
]


def survives(source: str, old: str, new: str, tests: str) -> bool:
    """Whether the tests still pass with `old` replaced by `new` in a copy of the committed tree."""
    with tempfile.TemporaryDirectory() as scratch:
        tree = Path(scratch) / "tree"
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", "HEAD"], check=True, capture_output=True).stdout
        tree.mkdir()
        subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
        shutil.copytree(ROOT / "apps" / "studio" / "dist", tree / "apps" / "studio" / "dist", dirs_exist_ok=True) if (
            ROOT / "apps/studio/dist"
        ).exists() else None
        path = tree / source
        text = path.read_text()
        if text.count(old) != 1:
            raise SystemExit(f"The mutation of {source} no longer applies: {old!r}")
        path.write_text(text.replace(old, new))
        env = os.environ | {"PYTHONPATH": os.pathsep.join([str(tree / "src"), str(tree / "tests")])}
        done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", tests], cwd=tree, env=env, capture_output=True, text=True)
        return done.returncode == 0


def main() -> int:
    survived = []
    for name, source, old, new, tests in MUTATIONS:
        alive = survives(source, old, new, tests)
        print(f"{'SURVIVED' if alive else 'killed  '}  {name}")
        survived += [name] if alive else []
    print(f"mutation pass: {len(MUTATIONS) - len(survived)} of {len(MUTATIONS)} killed")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())

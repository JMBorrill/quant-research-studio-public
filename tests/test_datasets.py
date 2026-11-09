"""Immutable datasets: content IDs, invalid-data rejection and tamper detection."""

import os
import warnings

import numpy as np
import pandas as pd
import pytest

from conftest import CLOSES, INSTRUMENT, OPENS, ohlc
from qrs import datasets
from qrs.datasets import DatasetError

META = {"name": "golden", "instrument": INSTRUMENT, "evidence_class": "synthetic", "source": "tests", "redistributable": True}


def test_round_trip_and_content_addressing(tmp_path):
    frame = ohlc(OPENS, CLOSES)
    first = datasets.save(tmp_path, frame, **META)
    assert datasets.save(tmp_path, frame, **META) == first  # identical content, same ID, no-op
    assert datasets.save(tmp_path, frame, **{**META, "name": "renamed"}) != first  # metadata is content
    bars = datasets.load(tmp_path, first)
    pd.testing.assert_frame_equal(bars.frame, frame.rename_axis("session"))
    assert bars.instrument == INSTRUMENT
    folder = datasets.directory(tmp_path, first)
    assert all(f.stat().st_mode & 0o222 == 0 for f in folder.iterdir())  # published read-only


def test_synthetic_generator_is_deterministic(tmp_path):
    assert datasets.create_synthetic(tmp_path / "a", seed=3, sessions=80) == datasets.create_synthetic(tmp_path / "b", seed=3, sessions=80)
    assert datasets.create_synthetic(tmp_path / "a", seed=4, sessions=80) != datasets.create_synthetic(tmp_path / "b", seed=3, sessions=80)


def test_the_stage_2_demo_dataset_keeps_its_id(tmp_path):
    # Datasets without an import report serialise exactly as before the report existed.
    dataset_id = datasets.create_synthetic(tmp_path)
    assert dataset_id == "sha256:91a9b5201e1f05417d89662020f3b5760a195d26518b8030daba8af04e7c9248"
    assert b"import_report" not in (datasets.directory(tmp_path, dataset_id) / "manifest.json").read_bytes()


@pytest.mark.parametrize(
    "edit, message",
    [
        (lambda f: f.assign(close=[np.nan, *CLOSES[1:]]), "1 session\\(s\\) have missing or non-finite prices"),
        (lambda f: f.assign(high=f["close"] - 1), "high must be >= open and close"),
        (lambda f: f.assign(open=[100.1, *OPENS[1:]]), "multiples of the tick size 0.25"),
        (lambda f: f.assign(low=-f["low"]), "prices must be positive"),
        (lambda f: f.iloc[::-1], "unique and in increasing order"),
        (lambda f: f.set_axis([f.index[0]] * len(f)), "unique and in increasing order"),
        (lambda f: f.set_axis(f.index + pd.Timedelta(hours=16)), "dates without a time of day"),
        (lambda f: f.drop(columns="low"), "columns must be exactly"),
        (lambda f: f.iloc[:1], "at least two sessions"),
        (lambda f: f.set_axis(f.index + pd.DateOffset(years=10)), "the contract expires 2030-12-31"),
    ],
)
def test_invalid_bars_are_rejected_with_a_reason(tmp_path, edit, message):
    with pytest.raises(DatasetError, match=message):
        datasets.save(tmp_path, edit(ohlc(OPENS, CLOSES)), **META)
    assert not (tmp_path / "datasets").exists() or not any(p for p in (tmp_path / "datasets").iterdir() if not p.name.startswith("."))


def test_non_finite_prices_are_reported_once_and_without_warnings(tmp_path):
    frame = ohlc(OPENS, CLOSES).assign(low=[-np.inf, *ohlc(OPENS, CLOSES)["low"][1:]])
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with pytest.raises(DatasetError) as rejected:
            datasets.save(tmp_path, frame, **META)
    assert str(rejected.value) == "Invalid bars: 1 session(s) have missing or non-finite prices."


def test_a_concurrent_save_of_the_same_content_is_not_an_error(tmp_path, monkeypatch):
    rename = os.rename

    def publish_competitor_first(source, target):
        monkeypatch.setattr(os, "rename", rename)
        competitor = datasets.save(tmp_path, ohlc(OPENS, CLOSES), **META)  # wins the race
        rename(source, target)  # fails: the target is already published
        return competitor

    monkeypatch.setattr(os, "rename", publish_competitor_first)
    dataset_id = datasets.save(tmp_path, ohlc(OPENS, CLOSES), **META)
    assert datasets.load(tmp_path, dataset_id).frame.equals(ohlc(OPENS, CLOSES).rename_axis("session"))
    assert [p.name for p in (tmp_path / "datasets").iterdir()] == [dataset_id.removeprefix("sha256:")]  # no stray temporary folder


@pytest.mark.parametrize("filename", ["bars.parquet", "manifest.json"])
def test_a_dataset_changed_in_place_fails_its_checksum(tmp_path, filename):
    dataset_id = datasets.save(tmp_path, ohlc(OPENS, CLOSES), **META)
    target = datasets.directory(tmp_path, dataset_id) / filename
    target.chmod(0o644)
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(DatasetError, match="changed after publication"):
        datasets.load(tmp_path, dataset_id)


def test_listing_skips_datasets_that_fail_verification(tmp_path):
    good = datasets.save(tmp_path, ohlc(OPENS, CLOSES), **META)
    bad = datasets.save(tmp_path, ohlc(OPENS, CLOSES), **{**META, "name": "tampered"})
    manifest = datasets.directory(tmp_path, bad) / "manifest.json"
    manifest.chmod(0o644)
    manifest.write_text("{}")
    (tmp_path / "datasets" / "stray-file.txt").write_text("not a dataset")
    assert list(datasets.list_manifests(tmp_path)) == [good]


def test_unknown_dataset(tmp_path):
    with pytest.raises(DatasetError, match="not found"):
        datasets.load(tmp_path, "sha256:" + "f" * 64)

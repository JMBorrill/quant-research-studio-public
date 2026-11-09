"""The analysis method registry (Stage 5). A method is a versioned Python function with declared
inputs, a Pydantic parameter model and declared outputs, registered like an operator. Methods know
nothing of any market: they read their inputs through the analysis context (qrs.analysis), which
applies every cutoff, and return a Result.

Methods may use SciPy, an optional extra (amendment A1); strategy runs never import this package."""

import importlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from qrs.contracts import MethodInfo
from qrs.registry import OPERATOR_ID, Params

MODULES = ("qrs.methods.describe", "qrs.methods.inference", "qrs.methods.selection", "qrs.methods.robustness")
# What a method tests, if anything: a "directional" test gives an estimate and a symmetric two-sided
# p-value, so a hypothesis may name a direction; a "joint" test (several restrictions at once) only a
# two-sided p-value. Only testing methods can test a registered hypothesis.
TESTS = (None, "directional", "joint")
INPUT_KINDS = ("bars", "observations", "equity", "record", "fills", "model")  # those that give one series
EXPERIMENT = ("experiment",)
RETURNS = ("bars", "equity")  # the input kinds that give session returns
OUTPUT_RIGHTS = ("rows", "aggregates")


class MethodError(ValueError):
    pass


def forward(returns: pd.Series, horizon: int, additive: bool) -> pd.Series:
    """At each session t, the return over the next `horizon` sessions, t + 1 to t + horizon: summed
    for price changes, compounded for fractions. Missing where those sessions are not all read, so a
    forward return never reaches past the cutoff."""
    if additive:
        return returns.rolling(horizon).sum().shift(-horizon)
    return ((1 + returns).rolling(horizon).apply(np.prod, raw=True) - 1).shift(-horizon)


def total(returns: pd.Series, additive: bool) -> float:
    """The return over all of `returns`: their sum, or their compounded product less one."""
    return float(returns.sum()) if additive else float(np.prod(1 + returns.to_numpy()) - 1)


@dataclass
class Result:
    """What a method found: JSON values (estimates, intervals, p-values, n and the effective n),
    warnings, and its declared tables and SVG figures."""

    values: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    figures: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Method:
    id: str
    version: int
    title: str
    description: str
    assumptions: tuple[str, ...]
    inputs: dict[str, tuple[str, ...]]  # input name -> the input kinds it accepts
    outputs: dict[str, str]  # table or figure name -> "rows" or "aggregates", for data rights
    params: type[Params]
    run: Callable[[Any, Any], Result]  # run(context, params) -> Result
    tests: str | None = None  # see TESTS

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def module(self) -> str:
        return self.run.__module__

    def source_file(self) -> str:
        return inspect.getsourcefile(self.run)

    def info(self) -> MethodInfo:
        return MethodInfo(
            id=self.id,
            version=self.version,
            title=self.title,
            description=self.description,
            inputs={name: list(kinds) for name, kinds in self.inputs.items()},
            outputs=self.outputs,
            tests=self.tests,
            params_schema=self.params.model_json_schema(),
        )


_methods: dict[tuple[str, int], Method] = {}
_loaded = False


def method(
    id: str,
    version: int,
    *,
    title: str,
    description: str,
    assumptions: tuple[str, ...],
    inputs: dict[str, tuple[str, ...]],
    outputs: dict[str, str],
    params: type[Params],
    tests: str | None = None,
):
    """Register the decorated run(context, params) function as a method."""
    if not OPERATOR_ID.match(id):
        raise ValueError(f"Method ID '{id}' must be namespaced, e.g. 'myteam.method'.")
    unknown = {k for kinds in inputs.values() for k in kinds} - {*INPUT_KINDS, *EXPERIMENT} | set(outputs.values()) - set(OUTPUT_RIGHTS)
    if unknown:
        raise ValueError(f"Method {id}@{version} declares unknown input kinds or output rights: {sorted(unknown)}")
    if tests not in TESTS:
        raise ValueError(f"Method {id}@{version} declares tests={tests!r}; supported: {TESTS}")
    if (id, version) in _methods:
        raise ValueError(f"Method {id}@{version} is already registered.")

    def register(fn):
        _methods[(id, version)] = Method(id, version, title, description, assumptions, inputs, outputs, params, fn, tests)
        return fn

    return register


def _load() -> None:
    global _loaded
    if not _loaded:
        try:
            for module in MODULES:
                importlib.import_module(module)
        except ModuleNotFoundError as e:
            if e.name == "scipy":
                raise MethodError("Analyses need SciPy: install the optional extra, qrs[research].") from None
            raise
        _loaded = True


def get(id: str, version: int) -> Method:
    _load()
    found = _methods.get((id, version))
    if found is None:
        versions = sorted(v for (i, v) in _methods if i == id)
        raise MethodError(f"Method {id} has no version {version} (available: {', '.join(map(str, versions))})." if versions else f"Unknown method {id}.")
    return found


def all_methods() -> list[Method]:
    _load()
    return sorted(_methods.values(), key=lambda m: (m.id, m.version))

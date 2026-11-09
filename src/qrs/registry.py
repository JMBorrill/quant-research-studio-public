"""The operator registry. An operator is a versioned Python function with declared typed ports, a
Pydantic parameter model and a temporal policy. The editor, validator and compiler only read the
registry, so adding an operator never requires changing them.

Built-in operators live in qrs.operators. Extra modules are listed (comma-separated) in the
QRS_OPERATOR_MODULES environment variable and imported on first use."""

import importlib
import inspect
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from qrs.contracts import OperatorInfo, PortInfo
from qrs.ports import PORT_TYPES

OPERATOR_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
# Parameters marked with this JSON Schema key hold a dataset ID (the editor offers a dataset picker);
# its value is the manifest kind the dataset must be, "qrs/dataset" (bars) or "qrs/observations".
DATASET_REF = "x-qrs-dataset"
# "same_index": every input and output shares the bars' session index. "as_of": the same, for an
# operator that also reads an observations dataset through the run context by publication time.
ALIGNMENTS = ("same_index", "as_of")


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


@dataclass(frozen=True)
class Temporal:
    """When an operator's outputs become valid. warm_up(params) gives the sessions before the first
    valid output: one number for every output, or a dict with a number per output port. A label
    output instead declares its horizon, the sessions until each target is known. Every operator
    must also be causal (its output at a session uses inputs at that session or earlier only; a
    label, its session and the next `horizon`). tests/test_operator_contracts.py checks both for
    every registered operator, running it with default parameters, so every parameter other than
    a dataset reference needs a default."""

    warm_up: Callable[[Any], int | dict[str, int]]
    alignment: str = "same_index"


@dataclass(frozen=True)
class Operator:
    id: str
    version: int
    title: str
    description: str
    family: str  # palette group, e.g. "indicators"; extensions may add their own
    inputs: dict[str, str]  # port name -> port type
    outputs: dict[str, str]
    params: type[Params]
    temporal: Temporal
    run: Callable[[Any, dict[str, Any], Any], dict[str, Any]]  # run(context, inputs, params) -> outputs

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    @property
    def module(self) -> str:
        return self.run.__module__

    def source_file(self) -> str:
        return inspect.getsourcefile(self.run)

    def output_warm_up(self, params: Params) -> dict[str, int]:
        """The declared warm-up of each output port, for validated params."""
        declared = self.temporal.warm_up(params)
        if isinstance(declared, int):
            return dict.fromkeys(self.outputs, declared)
        if set(declared) != set(self.outputs):
            raise ValueError(f"{self.key} declares warm-up for {sorted(declared)} but its outputs are {sorted(self.outputs)}.")
        return declared

    def dataset_params(self) -> list[str]:
        return [name for name, field in self.params.model_fields.items() if (field.json_schema_extra or {}).get(DATASET_REF)]

    def dataset_kind(self, param: str) -> str:
        """The manifest kind the dataset parameter `param` must refer to."""
        return self.params.model_fields[param].json_schema_extra[DATASET_REF]

    def info(self) -> OperatorInfo:
        return OperatorInfo(
            id=self.id,
            version=self.version,
            title=self.title,
            description=self.description,
            family=self.family,
            alignment=self.temporal.alignment,
            inputs=[PortInfo(name=k, type=v) for k, v in self.inputs.items()],
            outputs=[PortInfo(name=k, type=v) for k, v in self.outputs.items()],
            params_schema=self.params.model_json_schema(),
        )


_operators: dict[tuple[str, int], Operator] = {}
_loaded = False


def operator(
    id: str,
    version: int,
    *,
    title: str,
    description: str,
    family: str,
    inputs: dict[str, str],
    outputs: dict[str, str],
    params: type[Params],
    temporal: Temporal,
):
    """Register the decorated run(context, inputs, params) function as an operator."""
    if not OPERATOR_ID.match(id):
        raise ValueError(f"Operator ID '{id}' must be namespaced, e.g. 'myteam.indicator'.")
    if temporal.alignment not in ALIGNMENTS:
        raise ValueError(f"Operator {id}@{version} declares alignment '{temporal.alignment}'; supported: {', '.join(ALIGNMENTS)}.")
    unknown = {t for t in (*inputs.values(), *outputs.values()) if t not in PORT_TYPES}
    if unknown:
        raise ValueError(f"Operator {id}@{version} uses unknown port types: {sorted(unknown)}")
    if "label" in inputs.values() and any(t != "model" for t in outputs.values()):
        # Otherwise a target, which looks ahead, could pass on to a rule, a position or a backtest.
        raise ValueError(f"Operator {id}@{version} takes a label, so every output must be a model.")
    if (id, version) in _operators:
        raise ValueError(f"Operator {id}@{version} is already registered.")

    def register(fn):
        _operators[(id, version)] = Operator(id, version, title, description, family, inputs, outputs, params, temporal, fn)
        return fn

    return register


def _load() -> None:
    global _loaded
    if not _loaded:
        importlib.import_module("qrs.operators")
        for name in filter(None, (m.strip() for m in os.environ.get("QRS_OPERATOR_MODULES", "").split(","))):
            importlib.import_module(name)
        _loaded = True


def find(id: str, version: int) -> Operator | None:
    _load()
    return _operators.get((id, version))


def get(id: str, version: int) -> Operator:
    op = find(id, version)
    if op is None:
        raise KeyError(unknown_operator_message(id, version))
    return op


def unknown_operator_message(id: str, version: int) -> str:
    versions = sorted(v for (i, v) in _operators if i == id)
    if versions:
        return f"Operator {id} has no version {version} (available: {', '.join(map(str, versions))})."
    return f"Unknown operator {id}."


def all_operators() -> list[Operator]:
    _load()
    return sorted(_operators.values(), key=lambda o: (o.id, o.version))

"""Operators used only by the service tests: one that is slow (cancellation and time limits) and one
that kills its worker process (isolation)."""

import os
import signal
import time

from pydantic import Field

from qrs.registry import Params, Temporal, operator


class SleepParams(Params):
    seconds: float = Field(30, ge=0)
    ignore_terminate: bool = False  # like code that handles SIGTERM, so only a kill stops it


@operator(
    "tests.sleep",
    1,
    title="Sleep",
    description="Waits, then passes bars on.",
    inputs={"bars": "bars"},
    outputs={"bars": "bars"},
    family="tests",
    params=SleepParams,
    temporal=Temporal(warm_up=lambda p: 0),
)
def sleep(ctx, inputs, params: SleepParams):
    if params.ignore_terminate:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(params.seconds)
    return {"bars": inputs["bars"]}


@operator(
    "tests.abort",
    1,
    title="Abort",
    description="Kills the worker process.",
    inputs={"bars": "bars"},
    outputs={"bars": "bars"},
    family="tests",
    params=Params,
    temporal=Temporal(warm_up=lambda p: 0),
)
def abort(ctx, inputs, params):
    os.abort()

"""Position policies turn signals into target positions, decided at each session close and executed
at the next open by the backtest. Both policies use follow(), so they share one set of rules."""

import pandas as pd
from pydantic import Field

from qrs.registry import Params, Temporal, operator


def follow(long: pd.Series, short: pd.Series, exit_: pd.Series, quantity: int) -> pd.Series:
    """Target positions. An exit, or long and short entries on the same session, go flat. A long or
    short entry alone goes to +quantity or -quantity; holding the same side again adds nothing (no
    pyramiding). Otherwise the position is held. A missing signal (warm-up) counts as no signal."""
    held, targets = 0, []
    for go_long, go_short, leave in zip(long.fillna(False), short.fillna(False), exit_.fillna(False), strict=True):
        if leave or (go_long and go_short):
            held = 0
        elif go_long:
            held = quantity
        elif go_short:
            held = -quantity
        targets.append(held)
    return pd.Series(targets, index=long.index, dtype="int64")


class QuantityParams(Params):
    quantity: int = Field(1, ge=1, le=10_000, description="Contracts held while in a position.")


@operator(
    "qrs.long_flat",
    1,
    title="Long / flat",
    description=(
        "Starts flat. Goes long `quantity` contracts on an entry signal and returns to flat on an exit signal. "
        "Exit wins when both fire; no pyramiding; a missing signal is no signal."
    ),
    family="positions",
    inputs={"entry": "signal", "exit": "signal"},
    outputs={"positions": "positions"},
    params=QuantityParams,
    temporal=Temporal(warm_up=lambda p: 0),
)
def long_flat(ctx, inputs, params: QuantityParams):
    entry = inputs["entry"]
    never = pd.Series(False, index=entry.index)
    return {"positions": follow(entry, never, inputs["exit"], params.quantity)}


@operator(
    "qrs.long_short",
    1,
    title="Long / short",
    description=(
        "Starts flat. Goes long `quantity` contracts on a long signal and short `quantity` on a short signal, "
        "reversing in one order when already on the other side. An exit signal, or long and short together, "
        "goes flat; exit wins over any entry. No pyramiding; a missing signal is no signal."
    ),
    family="positions",
    inputs={"long": "signal", "short": "signal", "exit": "signal"},
    outputs={"positions": "positions"},
    params=QuantityParams,
    temporal=Temporal(warm_up=lambda p: 0),
)
def long_short(ctx, inputs, params: QuantityParams):
    return {"positions": follow(inputs["long"], inputs["short"], inputs["exit"], params.quantity)}

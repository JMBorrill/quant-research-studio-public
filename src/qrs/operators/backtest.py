from pydantic import Field

from qrs.analytics import metrics
from qrs.ports import BacktestResult
from qrs.registry import Params, Temporal, operator
from qrs.simulation import ASSUMPTIONS, simulate


class BacktestParams(Params):
    initial_cash: float = Field(100_000, gt=0, description="Starting account balance in the instrument currency.")
    commission_per_contract: float = Field(2.5, ge=0, description="Charged on every contract bought or sold.")
    slippage_ticks: int = Field(1, ge=0, le=100, description="Ticks added to buys and subtracted from sells at the open.")


@operator(
    "qrs.backtest",
    1,
    title="Backtest",
    description="Executes target positions on NautilusTrader at the next session's open and reports the ledger and metrics.",
    inputs={"bars": "bars", "positions": "positions"},
    family="evaluation",
    outputs={"result": "backtest"},
    params=BacktestParams,
    temporal=Temporal(warm_up=lambda p: 0),
)
def backtest(ctx, inputs, params: BacktestParams):
    bars = inputs["bars"]
    fills, equity = simulate(
        bars,
        inputs["positions"],
        initial_cash=params.initial_cash,
        commission_per_contract=params.commission_per_contract,
        slippage_ticks=params.slippage_ticks,
    )
    # A backtest never runs on a continuous series (Stage 5 A3), so leaving the field out keeps every
    # earlier run's summary, and its result hash, unchanged.
    instrument = bars.instrument.model_dump(mode="json", exclude={"continuous"})
    assumptions = {**ASSUMPTIONS, **params.model_dump(), "instrument": instrument}
    return {"result": BacktestResult(fills, equity, metrics(fills, equity, params.initial_cash), assumptions)}

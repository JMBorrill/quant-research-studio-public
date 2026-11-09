"""Simulation adapter: executes a target-position schedule on NautilusTrader. Nothing outside this
module touches Nautilus objects.

Execution model (reconciled with hand-calculated cases in tests/test_simulation.py):
- A target decided at a session's close is executed by a market order at the next session's open.
  The final session's target is never executed because there is no next open.
- Nautilus fills an order submitted from a bar at that bar's close, which would let close-derived
  signals trade on the same close. So the adapter feeds two quote events per session instead of
  bars: the open, quoted at open -/+ slippage (buys fill at the ask, sells at the bid), and the close
  at the close price, which marks open positions.
- Commission is a fixed amount per contract. Margin is a fixed fraction of notional value
  (StandardMarginModel); Nautilus's default model divides it by a leverage of 10.
- Nautilus accepts market orders whose initial margin exceeds equity, so the adapter checks initial
  margin before each order that opens, enlarges or reverses a position, and maintenance margin at
  each close, and fails the run on a breach. Orders that only reduce a position are always accepted.
"""

import warnings
from decimal import Decimal

import pandas as pd
from nautilus_trader.accounting.margin_models import StandardMarginModel
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import PerContractFeeModel
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType, AssetClass, OmsType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import FuturesContract
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy

from qrs.contracts import Fill, Instrument
from qrs.ports import Bars

VENUE = Venue("SIM")
CLOSE_TIME = pd.Timedelta(hours=23, minutes=59)  # session open is 00:00; only the order of events matters
DEPTH = Quantity.from_int(1_000_000)  # quoted size, deep enough that every order fills in full
FILL_COLUMNS = list(Fill.model_fields)
ASSUMPTIONS = {
    "fills": "Market orders at the next session's open; buys at open + slippage, sells at open - slippage.",
    "signals": "Targets are decided at the session close; the final session's target is not executed.",
    "commission": "Fixed amount per contract per fill.",
    "margin": "Initial and maintenance margin as fixed fractions of notional; a breach fails the run.",
    "valuation": "Positions are marked at each session's close.",
    "engine": "NautilusTrader backtest engine, netting OMS, margin account.",
}


class SimulationError(RuntimeError):
    pass


def futures_contract(spec: Instrument) -> FuturesContract:
    precision = max(0, -Decimal(repr(spec.tick_size)).as_tuple().exponent)
    return FuturesContract(
        instrument_id=InstrumentId(Symbol(spec.symbol), VENUE),
        raw_symbol=Symbol(spec.symbol),
        asset_class=AssetClass.COMMODITY,
        currency=Currency.from_str(spec.currency),
        price_precision=precision,
        price_increment=Price(spec.tick_size, precision),
        multiplier=Quantity.from_int(spec.multiplier),
        lot_size=Quantity.from_int(1),
        underlying=spec.symbol,
        activation_ns=0,
        expiration_ns=pd.Timestamp(spec.expiry).value,
        ts_event=0,
        ts_init=0,
        margin_init=Decimal(repr(spec.margin_init)),
        margin_maint=Decimal(repr(spec.margin_maint)),
        maker_fee=Decimal(0),
        taker_fee=Decimal(0),
    )


class _TargetFollower(Strategy):
    """Trades the difference between the current position and the planned target at each open."""

    def __init__(self, instrument: FuturesContract, spec: Instrument, bars: pd.DataFrame, targets: list[int], events: dict[int, tuple[int, bool]]):
        super().__init__()
        self.contract = instrument
        self.spec = spec
        self.bars = bars
        self.targets = targets
        self.events = events  # quote timestamp -> (session index, is_open)
        self.session = 0
        self.fill_rows: list[dict] = []
        self.mark_rows: list[dict] = []
        self.failure: str | None = None

    def on_start(self) -> None:
        self.subscribe_quote_ticks(self.contract.id)

    def on_quote_tick(self, tick: QuoteTick) -> None:
        if self.failure:
            return
        self.session, is_open = self.events[tick.ts_event]
        if is_open:
            self._rebalance(tick)
        else:
            self._mark()

    def _position(self) -> int:
        return int(self.portfolio.net_position(self.contract.id))

    def _balances(self) -> tuple[float, float]:
        balance = self.portfolio.account(VENUE).balance_total(self.contract.quote_currency).as_double()
        unrealized = self.portfolio.unrealized_pnl(self.contract.id)
        return balance, unrealized.as_double() if unrealized is not None else 0.0

    def _date(self) -> str:
        return str(self.bars.index[self.session].date())

    def _rebalance(self, tick: QuoteTick) -> None:
        if self.session == 0:
            return
        target = self.targets[self.session - 1]
        current = self._position()
        delta = target - current
        if delta == 0:
            return
        if abs(target) > abs(current) or target * current < 0:
            price = (tick.ask_price if delta > 0 else tick.bid_price).as_double()
            required = abs(target) * price * self.spec.multiplier * self.spec.margin_init
            equity = sum(self._balances())
            if required > equity:
                self.failure = (
                    f"Insufficient capital on {self._date()}: holding {target} contract(s) needs initial margin "
                    f"{required:,.2f} {self.spec.currency} but equity is {equity:,.2f}."
                )
                return
        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        self.submit_order(self.order_factory.market(self.contract.id, side, Quantity.from_int(abs(delta))))

    def _mark(self) -> None:
        position = self._position()
        planned = self.targets[self.session - 1] if self.session else 0
        if position != planned:
            self.failure = f"Position on {self._date()} is {position} but the plan required {planned}; an order did not fill."
            return
        cash, unrealized = self._balances()
        equity = cash + unrealized
        maintenance = abs(position) * self.bars["close"].iloc[self.session] * self.spec.multiplier * self.spec.margin_maint
        if equity < maintenance:
            self.failure = f"Maintenance margin breached on {self._date()}: equity {equity:,.2f} is below {maintenance:,.2f} {self.spec.currency}."
            return
        self.mark_rows.append({"session": self.bars.index[self.session], "position": position, "cash": cash, "unrealized_pnl": unrealized, "equity": equity})

    def on_order_filled(self, event) -> None:
        price = event.last_px.as_double()
        quantity = int(event.last_qty)
        self.fill_rows.append(
            {
                "session": self.bars.index[self.session],
                "side": "BUY" if event.order_side == OrderSide.BUY else "SELL",
                "quantity": quantity,
                "price": price,
                "commission": event.commission.as_double(),
                "slippage": abs(price - self.bars["open"].iloc[self.session]) * quantity * self.spec.multiplier,
            }
        )

    def on_order_denied(self, event) -> None:
        self.failure = f"Order denied on {self._date()}: {event.reason}"

    def on_order_rejected(self, event) -> None:
        self.failure = f"Order rejected on {self._date()}: {event.reason}"


def simulate(bars: Bars, targets: pd.Series, *, initial_cash: float, commission_per_contract: float, slippage_ticks: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (fills, equity): one row per fill, and one row per session with position, cash,
    unrealized P&L and equity at the close."""
    if not targets.index.equals(bars.frame.index):
        raise SimulationError("Target positions must use exactly the bars' session index.")
    spec = bars.instrument
    contract = futures_contract(spec)
    currency = contract.quote_currency
    slip = slippage_ticks * spec.tick_size
    precision = contract.price_precision

    quotes, events = [], {}
    for i, (session, bar) in enumerate(bars.frame.iterrows()):
        opened, closed = session.value, (session + CLOSE_TIME).value
        events[opened], events[closed] = (i, True), (i, False)
        quotes.append(QuoteTick(contract.id, Price(bar.open - slip, precision), Price(bar.open + slip, precision), DEPTH, DEPTH, opened, opened))
        quotes.append(QuoteTick(contract.id, Price(bar.close, precision), Price(bar.close, precision), DEPTH, DEPTH, closed, closed))

    # Nautilus's logger can be initialised only once per process, which breaks a second backtest in the
    # same process; the adapter reports every failure itself, so Nautilus logging is bypassed.
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)))
    try:
        engine.add_venue(
            VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(initial_cash, currency)],
            base_currency=currency,
            margin_model=StandardMarginModel(),
            fee_model=PerContractFeeModel(Money(commission_per_contract, currency)),
        )
        engine.add_instrument(contract)
        engine.add_data(quotes)
        follower = _TargetFollower(contract, spec, bars.frame, [int(t) for t in targets], events)
        engine.add_strategy(follower)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # Nautilus calls deprecated pandas APIs internally
            engine.run()
    finally:
        engine.dispose()

    if follower.failure:
        raise SimulationError(follower.failure)
    fills = pd.DataFrame(follower.fill_rows, columns=FILL_COLUMNS)
    equity = pd.DataFrame(follower.mark_rows).set_index("session")
    return fills, equity

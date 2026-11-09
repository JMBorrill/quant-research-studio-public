"""Risk and performance metrics, derived only from the simulation ledger (fills and equity)."""

import pandas as pd


def metrics(fills: pd.DataFrame, equity: pd.DataFrame, initial_cash: float, drawdown_from_initial: bool = False) -> dict:
    """With `drawdown_from_initial`, drawdowns are measured from `initial_cash` as well as from the
    curve: for a slice measured from the close before it (the holdout), whose first close may already
    be below that baseline. A whole run's are measured from its first close alone, which the
    simulator may read back a hair below `initial_cash` (484999.99999999994 for 485000)."""
    curve = equity["equity"]
    peak = curve.cummax().clip(lower=initial_cash) if drawdown_from_initial else curve.cummax()
    drawdown = curve - peak
    underwater = (drawdown < 0).astype(int)
    # Longest run of consecutive sessions below the previous peak.
    runs = underwater.groupby((underwater == 0).cumsum()).sum()
    signed = fills["quantity"].where(fills["side"] == "BUY", -fills["quantity"])
    # A slice of a run may open with a position already held; a whole run opens flat. The opening
    # position is the first session's, less any of the given fills made on it. A round trip ends each
    # time the position returns to flat.
    opening = int(equity["position"].iloc[0]) - int(signed[fills["session"] == equity.index[0]].sum())
    positions = opening + signed.cumsum()
    commission = float(fills["commission"].sum())
    slippage = float(fills["slippage"].sum())
    net_pnl = float(curve.iloc[-1]) - initial_cash
    return {
        "sessions": len(equity),
        "initial_cash": initial_cash,
        "final_equity": float(curve.iloc[-1]),
        "net_pnl": net_pnl,
        "gross_pnl": net_pnl + commission + slippage,
        "commission": commission,
        "slippage": slippage,
        "return_pct": 100 * net_pnl / initial_cash,
        "max_drawdown": float(abs(drawdown.min())),
        "max_drawdown_pct": float(abs((drawdown / peak).min()) * 100),
        "max_drawdown_sessions": int(runs.max()),
        "exposure_pct": float((equity["position"] != 0).mean() * 100),
        "fills": len(fills),
        "round_trips": int((positions == 0).sum()),
        "contracts_traded": int(fills["quantity"].sum()),
        "final_position": int(equity["position"].iloc[-1]),
    }

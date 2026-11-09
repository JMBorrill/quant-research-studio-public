"""Statistical inference for analyses (Stage 5 workstream 5). Distributions come from SciPy
(amendment A1); the procedures it lacks are written here against their published definitions, and
tests/test_inference.py checks each against reference values from statsmodels and arch.

- Newey–West (HAC) covariance with Bartlett weights, and the plan's lag for overlapping horizons.
- Ordinary least squares with HAC errors scaled by n / (n − k), Student t tests on n − k degrees of
  freedom, and the Wald test of linear restrictions, against χ² or, in small samples, F.
- The stationary bootstrap (Politis and Romano 1994), with its block length chosen as Politis and
  White (2004) propose, with the correction of Patton, Politis and White (2009).
- Holm's step-down adjustment, and Benjamini–Hochberg and Benjamini–Yekutieli from SciPy.

SciPy is imported only by the functions that use it: projects use this module for families'
conclusions, and the CLI and the service load projects without the optional extra."""

import math
from collections.abc import Callable, Iterator

import numpy as np

BOOTSTRAP_CHUNK = 200  # replicates drawn at once, to bound memory


def newey_west_lag(n: int, horizon: int = 1) -> int:
    """The plan's lag: floor(4 (n / 100)^(2/9)), and at least h − 1 for overlapping h-session returns."""
    return max(math.floor(4 * (n / 100) ** (2 / 9)), horizon - 1)


def hac_covariance(x: np.ndarray, residuals: np.ndarray, lag: int) -> np.ndarray:
    """The Newey–West estimate of the coefficients' covariance, (X'X)⁻¹ S (X'X)⁻¹, where S sums the
    scores' autocovariances to `lag` with Bartlett weights 1 − l / (lag + 1), scaled by n / (n − k) as
    statsmodels does with its small-sample correction. HAC tests still reject more often than their
    level in small autocorrelated samples (tests/test_inference.py measures it)."""
    scores = x * residuals[:, None]
    s = scores.T @ scores
    for lag_l in range(1, lag + 1):
        gamma = scores[lag_l:].T @ scores[:-lag_l]
        s += (1 - lag_l / (lag + 1)) * (gamma + gamma.T)
    bread = np.linalg.inv(x.T @ x)
    n, k = x.shape
    return bread @ s @ bread * n / (n - k)


def ols(y: np.ndarray, x: np.ndarray, lag: int) -> dict:
    """Least squares of y on the columns of x, with HAC standard errors and two-sided Student t
    p-values on n − k degrees of freedom."""
    from scipy import stats

    n, k = x.shape
    beta, *_ = np.linalg.lstsq(x, y, rcond=None)
    residuals = y - x @ beta
    covariance = hac_covariance(x, residuals, lag)
    se = np.sqrt(np.diag(covariance))
    t = beta / se
    return {
        "beta": beta,
        "se": se,
        "t": t,
        "p": 2 * stats.t.sf(np.abs(t), n - k),
        "df": n - k,
        "covariance": covariance,
        "r_squared": 1 - math.fsum(residuals**2) / math.fsum((y - y.mean()) ** 2) if n > 1 and np.ptp(y) > 0 else None,
    }


def t_interval(estimate: float, se: float, df: int, level: float) -> tuple[float, float]:
    from scipy import stats

    half = stats.t.ppf(0.5 + level / 2, df) * se
    return estimate - half, estimate + half


def wald(beta: np.ndarray, covariance: np.ndarray, restrictions: np.ndarray, df_denominator: int | None = None) -> tuple[float, int, float]:
    """The Wald test that R β = 0, for q restrictions: the χ² statistic W, q and its p-value; or, given
    `df_denominator`, the F statistic W / q and its p-value on (q, df_denominator) degrees of freedom."""
    from scipy import stats

    r = restrictions @ beta
    statistic = float(r @ np.linalg.solve(restrictions @ covariance @ restrictions.T, r))
    q = restrictions.shape[0]
    if df_denominator is None:
        return statistic, q, float(stats.chi2.sf(statistic, q))
    return statistic / q, q, float(stats.f.sf(statistic / q, q, df_denominator))


def holm(p_values: list[float]) -> list[float]:
    """Holm's step-down adjusted p-values: the i-th smallest times m − i + 1, kept increasing, at most 1."""
    order = np.argsort(p_values, kind="stable")
    m = len(p_values)
    adjusted, running = [0.0] * m, 0.0
    for i, index in enumerate(order):
        running = max(running, min(1.0, (m - i) * p_values[index]))
        adjusted[index] = running
    return adjusted


def adjust(p_values: list[float], correction: str) -> list[float]:
    """Adjusted p-values: Holm, Benjamini–Hochberg ("bh") or Benjamini–Yekutieli ("by")."""
    if correction == "holm":
        return holm(p_values)
    from scipy import stats

    return [float(p) for p in stats.false_discovery_control(p_values, method=correction)]


def optimal_block_length(x: np.ndarray) -> float:
    """The stationary bootstrap's expected block length for series x: Politis and White (2004) with
    the correction of Patton, Politis and White (2009), exactly as the arch package computes it,
    including its choice of the window of insignificant autocorrelations. At least 1."""
    n = len(x)
    centred = x - x.mean()
    if not centred.any():  # a constant series: any block resamples it the same
        return 1.0
    k_n = max(5, int(np.log10(n)))
    m_max = math.ceil(math.sqrt(n)) + k_n
    bound = 2 * math.sqrt(math.log10(n) / n)
    autocovariance, correlation, m_hat = np.zeros(m_max + 1), np.zeros(m_max + 1), None
    for i in range(m_max + 1):
        cross = centred[i:] @ centred[: n - i]
        autocovariance[i] = cross / n
        scale = math.sqrt((centred[i + 1 :] @ centred[i + 1 :]) * (centred[: -(i + 1)] @ centred[: -(i + 1)]))
        correlation[i] = abs(cross) / scale if scale > 0 else 0.0
        if i >= k_n and m_hat is None and (correlation[i - k_n : i] < bound).all():
            m_hat = i - k_n
    m = min(2 * max(m_hat, 1) if m_hat is not None else m_max, m_max)
    weights = [1.0 if k / m <= 0.5 else 2 * (1 - k / m) for k in range(1, m + 1)]
    g = sum(2 * w * k * autocovariance[k] for k, w in enumerate(weights, 1))
    long_run = autocovariance[0] + sum(2 * w * autocovariance[k] for k, w in enumerate(weights, 1))
    block = (2 * g**2 / (2 * long_run**2)) ** (1 / 3) * n ** (1 / 3)
    return float(max(1.0, min(block, math.ceil(min(3 * math.sqrt(n), n / 3)))))


def stationary_indices(n: int, block: float, replicates: int, rng: np.random.Generator) -> Iterator[np.ndarray]:
    """Resampled indices of a series of length n, in chunks of replicates: blocks starting at uniform
    positions, with geometric lengths of mean `block`, wrapping round the end (Politis and Romano)."""
    for start in range(0, replicates, BOOTSTRAP_CHUNK):
        rows = min(BOOTSTRAP_CHUNK, replicates - start)
        fresh = rng.random((rows, n)) < 1 / block
        fresh[:, 0] = True
        starts = rng.integers(0, n, (rows, n))
        positions = np.broadcast_to(np.arange(n), (rows, n))
        block_start = np.maximum.accumulate(np.where(fresh, positions, 0), axis=1)
        first = np.take_along_axis(starts, block_start, axis=1)
        yield (first + positions - block_start) % n


def bootstrap(columns: np.ndarray, statistic: Callable[[np.ndarray], np.ndarray], replicates: int, seed: int, block: float | None = None) -> np.ndarray:
    """The statistic of each stationary-bootstrap resample of the rows of `columns` (sessions by
    series, resampled together). `statistic` maps an array of shape (replicates, sessions, series) to
    one value per replicate. The block length is chosen from the first column unless given."""
    data = columns if columns.ndim == 2 else columns[:, None]
    block = block or optimal_block_length(data[:, 0])
    rng = np.random.default_rng(seed)
    return np.concatenate([statistic(data[indices]) for indices in stationary_indices(len(data), block, replicates, rng)])


def percentile_interval(values: np.ndarray, level: float) -> tuple[float, float]:
    low, high = np.quantile(values, [0.5 - level / 2, 0.5 + level / 2])
    return float(low), float(high)


def one_sided(p_two_sided: float, estimate: float, direction: str) -> float:
    """A symmetric test's p-value in the registered direction: half the two-sided one when the
    estimate lies that way, and one less half of it when it does not."""
    if direction == "two_sided":
        return p_two_sided
    agrees = estimate > 0 if direction == "greater" else estimate < 0
    return p_two_sided / 2 if agrees else 1 - p_two_sided / 2


EULER_GAMMA = 0.5772156649015329


def sharpe_moments(returns: np.ndarray) -> tuple[float, float, float]:
    """The per-session Sharpe ratio (mean over sample deviation), skewness and kurtosis (not excess;
    population moments), as Bailey and López de Prado use them. Returns that never vary (a strategy
    that never traded) have a Sharpe ratio of 0 and no higher moments."""
    from scipy import stats

    if np.ptp(returns) == 0:
        return 0.0, float("nan"), float("nan")
    return float(returns.mean() / returns.std(ddof=1)), float(stats.skew(returns)), float(stats.kurtosis(returns, fisher=False))


def probabilistic_sharpe(sharpe: float, benchmark: float, sessions: int, skewness: float, kurtosis: float) -> float:
    """The probability that the true per-session Sharpe ratio exceeds `benchmark`, given an estimate
    from `sessions` returns with this skewness and kurtosis (Bailey and López de Prado 2012). It
    assumes serially independent returns."""
    from scipy import stats

    spread = math.sqrt(1 - skewness * sharpe + (kurtosis - 1) / 4 * sharpe**2)
    return float(stats.norm.cdf((sharpe - benchmark) * math.sqrt(sessions - 1) / spread))


def expected_maximum_sharpe(variance: float, trials: int) -> float:
    """The expected largest of `trials` per-session Sharpe ratios whose true values are all zero and
    whose estimates vary with `variance` (Bailey and López de Prado 2014): the deflated benchmark."""
    from scipy import stats

    return math.sqrt(variance) * ((1 - EULER_GAMMA) * stats.norm.ppf(1 - 1 / trials) + EULER_GAMMA * stats.norm.ppf(1 - 1 / (trials * math.e)))


def reality_check(differences: np.ndarray, replicates: int, seed: int) -> dict:
    """White's (2000) Reality Check that no column of `differences` (sessions by strategies, each a
    strategy's return less the benchmark's) has a positive mean: V = max over strategies of √T times
    its mean, against the stationary-bootstrap distribution of the same maximum of recentred means.
    One set of resampled sessions is shared by every strategy; the block length is the median of the
    strategies' optimal lengths."""
    if not np.isfinite(differences).all():  # a missing value would make every comparison false, and p its least
        raise ValueError("The Reality Check needs finite returns for every strategy on every session.")
    sessions = len(differences)
    means = differences.mean(axis=0)
    statistic = float(math.sqrt(sessions) * means.max())
    block = float(np.median([optimal_block_length(column) for column in differences.T]))
    rng = np.random.default_rng(seed)
    resampled = []
    for indices in stationary_indices(sessions, block, replicates, rng):
        counts = np.stack([np.bincount(row, minlength=sessions) for row in indices])
        resampled.append(math.sqrt(sessions) * ((counts @ differences) / sessions - means).max(axis=1))
    maxima = np.concatenate(resampled)
    # Counting the sample itself among the resamples, so the p-value is never 0.
    p_value = (1 + int((maxima >= statistic).sum())) / (1 + replicates)
    return {"statistic": statistic, "p_value": p_value, "block": block, "best": int(means.argmax())}

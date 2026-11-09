"""Writes tests/reference/inference.json: inputs, and the reference values statsmodels and arch give
for them, which tests/test_inference.py checks qrs.stats against. Neither package is a dependency of
QRS; the file was written with statsmodels 0.15.0 and arch 8.0.0:

    python tests/reference/inference.py > tests/reference/inference.json"""

import json

import numpy as np
import statsmodels.api as sm
from arch.bootstrap import SPA, optimal_block_length
from statsmodels.stats.multitest import multipletests

rng = np.random.default_rng(20261102)
n = 300
e = rng.normal(size=n)
ar = np.zeros(n)
for t in range(1, n):
    ar[t] = 0.5 * ar[t - 1] + e[t]
x = rng.normal(size=n) + 0.3 * ar
y = 0.2 + 0.1 * x + ar
out = {"ar": ar.tolist(), "x": x.tolist(), "y": y.tolist()}
# Mean test: y on a constant, HAC lag 5, t with n-1 df.
fit = sm.OLS(ar, np.ones((n, 1))).fit(cov_type="HAC", cov_kwds={"maxlags": 5, "use_correction": True}, use_t=True)
out["mean"] = {"beta": fit.params.tolist(), "se": fit.bse.tolist(), "p": fit.pvalues.tolist()}
X = sm.add_constant(x)
fit = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": 7, "use_correction": True}, use_t=True)
out["regression"] = {"beta": fit.params.tolist(), "se": fit.bse.tolist(), "p": fit.pvalues.tolist(), "r2": fit.rsquared}
# Wald: four group means equal, groups by position mod 4, HAC lag 4, chi2.
groups = np.arange(n) % 4
D = np.eye(4)[groups]
fit = sm.OLS(ar, D).fit(cov_type="HAC", cov_kwds={"maxlags": 4, "use_correction": True})
R = np.hstack([np.eye(3), -np.ones((3, 1))])
w = fit.wald_test(R, use_f=False, scalar=True)
f = fit.wald_test(R, use_f=True, scalar=True)
out["wald"] = {"statistic": float(w.statistic), "df": 3, "p": float(w.pvalue), "f": float(f.statistic)}
ps = [0.01, 0.04, 0.03, 0.005, 0.2, 0.049]
out["p_values"] = ps
for method, name in (("holm", "holm"), ("fdr_bh", "bh"), ("fdr_by", "by")):
    out[name] = multipletests(ps, method=method)[1].tolist()
out["block"] = {"ar": float(optimal_block_length(ar)["stationary"].iloc[0]), "x": float(optimal_block_length(x)["stationary"].iloc[0])}
# White's Reality Check: arch's SPA without studentizing, whose "upper" p-value is White's, on three
# strategies' returns against a zero benchmark (losses are negated returns), shifted towards an edge.
# Its resampling differs from QRS's, so the test compares p-values within Monte Carlo error.
reality = {"shifts": [0.0, 0.05, 0.1, 0.15], "reps": 20000, "p": []}
for shift in reality["shifts"]:
    d = np.column_stack([ar * 0.5 + shift, x, y - y.mean() + shift / 2])
    reality["block"] = float(np.median([optimal_block_length(c)["stationary"].iloc[0] for c in d.T]))
    spa = SPA(np.zeros(n), -d, block_size=reality["block"], reps=reality["reps"], studentize=False, seed=1)
    spa.compute()
    reality["p"].append(float(spa.pvalues["upper"]))
out["reality_check"] = reality
print(json.dumps(out))

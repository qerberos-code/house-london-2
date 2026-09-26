"""Robustness of the two headline estimates to sample, outcome and measurement choices.

Headline estimates (see PLAN.md §2):
  total   = coefficient on z_rate in the main model (no time fixed effects)     -> "rates matter"
  within  = coefficient on z_rate:sale with approval-year fixed effects          -> "demand vs finance" test
  sales   = coefficient on z_sales (local home sales), with year fixed effects  -> direct demand test
Output: outputs/robustness.csv
Run: .venv/bin/python robustness.py
"""
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

import pipeline as P

OUT = Path(__file__).parent / "outputs"
CTRL = "sale + sale_missing + z_units + tall + storeys_missing + z_hpi"
TOTAL = f"lapsed ~ {CTRL} + z_rate*sale + z_cost*z_hpi + z_sales*sale"
WITHIN = f"lapsed ~ {CTRL} + z_rate:sale + z_cost:z_hpi + z_sales*sale + C(year)"
TOKEN_WINDOW = (1035, 1095)  # a start in the last 60 days before the 3-year deadline


def base_schemes() -> pd.DataFrame:
    s = P.load_schemes()
    s = s[s.approval_month.between(pd.Period(P.FIRST), pd.Period(P.LAST))].copy()
    s["start_days"] = (pd.to_datetime(s.actual_commencement_date, format="%d/%m/%Y", errors="coerce")
                       - s.decision_date).dt.days
    return s


def make_panel(s: pd.DataFrame, superseded=None, token_as_lapse=False, rate_col="mortgage_2y_75ltv") -> pd.DataFrame:
    """superseded: None = drop (main), 0 = count as started, 1 = count as lapsed."""
    keep = ["Completed", "Commenced", "Lapsed"] + (["Superseded"] if superseded is not None else [])
    d = s[s.status.isin(keep)].copy()
    d["lapsed"] = (d.status == "Lapsed").astype(int)
    if superseded is not None:
        d.loc[d.status == "Superseded", "lapsed"] = superseded
    if token_as_lapse:
        token = d.start_days.between(*TOKEN_WINDOW) & (d.status != "Completed")
        d.loc[token, "lapsed"] = 1
    d = P.attach_market(P.add_scheme_features(d)).dropna(subset=["hpi_price"])
    if rate_col != "mortgage_2y_75ltv":
        series = pd.read_csv(P.DATA / "mortgage_rates_monthly.csv", index_col="month")[rate_col]
        series.index = pd.PeriodIndex(series.index, freq="M")
        d["mortgage_rate_window"] = d.approval_month.map(lambda m: P.window_mean(series, m, P.WINDOW))
    d["year"] = d.approval_month.dt.year
    d["sale_missing"] = d.sale_share.isna().astype(int)
    d["sale"] = d.sale_share.fillna(d.sale_share.median())
    d["borough_key"] = d.borough_key.fillna("unknown")
    for z, raw in {"z_rate": d.mortgage_rate_window, "z_cost": d.build_cost_change, "z_sales": np.log(d.sales_index_window),
                   "z_hpi": np.log(d.hpi_price), "z_units": d.log_units}.items():
        d[z] = (raw - raw.mean()) / raw.std()
    return d


def fit(d: pd.DataFrame, formula: str):
    return smf.logit(formula, d).fit(disp=0, cov_type="cluster", cov_kwds={"groups": pd.factorize(d.borough_key)[0]})


if __name__ == "__main__":
    s = base_schemes()
    variants = {
        "Main specification": make_panel(s),
        "Complete cases only (tenure known)": make_panel(s).query("sale_missing == 0"),
        "Superseded counted as started": make_panel(s, superseded=0),
        "Superseded counted as lapsed": make_panel(s, superseded=1),
        "Late token starts counted as lapsed": make_panel(s, token_as_lapse=True),
        "Rate = 95% LTV mortgage": make_panel(s, rate_col="mortgage_2y_95ltv"),
        "Rate = SONIA (developer finance proxy)": make_panel(s, rate_col="sonia"),
    }
    rows = []
    for name, d in variants.items():
        f_total = TOTAL.replace("sale_missing + ", "") if d.sale_missing.eq(0).all() else TOTAL
        f_within = WITHIN.replace("sale_missing + ", "") if d.sale_missing.eq(0).all() else WITHIN
        total, within = fit(d, f_total), fit(d, f_within)
        rows.append({"variant": name, "n": len(d), "lapse_rate": d.lapsed.mean(),
                     "rate_effect": total.params["z_rate"], "rate_se": total.bse["z_rate"],
                     "rate_x_sale_within_year": within.params["z_rate:sale"], "rate_x_sale_se": within.bse["z_rate:sale"],
                     "sales_within_year": within.params["z_sales"], "sales_se": within.bse["z_sales"]})
    res = pd.DataFrame(rows).round(3)
    OUT.mkdir(exist_ok=True)
    res.to_csv(OUT / "robustness.csv", index=False)
    print(res.to_string(index=False))

"""Descriptive charts + lapse model on the scheme panel (see PLAN.md §2 for the variables).

Input:  data/scheme_panel.parquet   (pipeline.py)
Output: outputs/*.png               descriptive charts
        outputs/marginal_effects.csv  effects in percentage points with 90% cluster-bootstrap intervals
        outputs/decomposition.csv     2020-22 counterfactuals: rates, costs, local sales held at 2014-19 levels
        outputs/model_results.json  coefficients + scaling, consumed by the simulator
Run: .venv/bin/python analysis.py
"""
import json
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from sklearn.metrics import roc_auc_score

matplotlib.use("Agg")
ROOT = Path(__file__).parent
OUT = ROOT / "outputs"
BLUE, ORANGE, AQUA, INK, INK2, GRID = "#2a78d6", "#eb6834", "#1baf7a", "#0b0b0b", "#52514e", "#e4e3df"

FORMULA = ("lapsed ~ sale + sale_missing + z_units + tall + storeys_missing + z_hpi"
           " + z_rate * sale + z_cost * z_hpi + z_sales * sale")
BOOT_REPS = 200
SCALED = {"z_rate": "mortgage_rate_window", "z_cost": "build_cost_change", "z_sales": "log_sales",
          "z_hpi": "log_hpi", "z_units": "log_units"}
# Shocks reported in percentage points: (label, variable, change in raw units of that variable)
SHOCKS = [("+1pp mortgage rate", "z_rate", 1.0, BLUE),
          ("+10pp build-cost rise", "z_cost", 0.10, ORANGE),
          ("−20% local home sales", "z_sales", np.log(0.8), AQUA)]
MARKET = {"rates": ("z_rate", "mortgage_rate_window"), "costs": ("z_cost", "build_cost_change"),
          "local sales": ("z_sales", "log_sales")}


def load() -> pd.DataFrame:
    p = pd.read_parquet(ROOT / "data" / "scheme_panel.parquet").dropna(subset=["hpi_price"])
    p["year"] = p.approval_month.str[:4].astype(int)
    p["sale_missing"] = p.sale_share.isna().astype(int)
    p["sale"] = p.sale_share.fillna(p.sale_share.median())
    p["log_hpi"] = np.log(p.hpi_price)
    p["log_sales"] = np.log(p.sales_index_window)
    p["borough_key"] = p.borough_key.fillna("unknown")
    return p


def standardise(p: pd.DataFrame) -> dict:
    scaling = {}
    for z, raw in SCALED.items():
        mu, sd = p[raw].mean(), p[raw].std()
        p[z] = (p[raw] - mu) / sd
        scaling[z] = {"raw": raw, "mean": mu, "std": sd}
    return scaling


# ---------- charts ----------
def style(ax, title, ylabel):
    ax.set_title(title, loc="left", color=INK, fontsize=12, pad=10)
    ax.set_ylabel(ylabel, color=INK2)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]:
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2)


def chart_trend(p):
    """Two panels, one axis each: lapse rate by cohort, and the mortgage rate that cohort faced."""
    g = p.groupby("year").agg(lapse=("lapsed", "mean"), rate=("mortgage_rate_window", "mean"), n=("lapsed", "size"))
    fig, (a, b) = plt.subplots(2, 1, figsize=(8, 6.5), sharex=True, gridspec_kw={"hspace": 0.35})
    a.bar(g.index, g.lapse * 100, color=BLUE, width=0.6)
    for x, y in zip(g.index, g.lapse * 100):
        a.text(x, y + 0.8, f"{y:.0f}%", ha="center", color=INK, fontsize=9)
    style(a, "Share of approved schemes (10+ homes) that lapsed unbuilt", "% lapsed")
    b.plot(g.index, g.rate, color=BLUE, linewidth=2, marker="o", markersize=6)
    style(b, "Average 2-year fixed mortgage rate in the 3 years after approval", "% rate")
    b.set_xlabel("Approval year", color=INK2)
    fig.savefig(OUT / "1_lapse_vs_rates.png", dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def chart_split(p, groups, labels, title, fname):
    fig, ax = plt.subplots(figsize=(8, 4))
    series = [p[mask].groupby("year").lapsed.mean() * 100 for mask in groups]
    ends = [s.values[-1] for s in series]
    nudge = max(0, 3 - abs(ends[0] - ends[1])) / 2  # keep end labels from colliding
    for s, label, color, end in zip(series, labels, [ORANGE, BLUE], ends):
        ax.plot(s.index, s.values, color=color, linewidth=2, marker="o", markersize=6, label=label)
        y = end + (nudge if end >= max(ends) else -nudge)
        ax.text(s.index[-1] + 0.15, y, label, color=INK, va="center", fontsize=9)
    style(ax, title, "% lapsed")
    ax.legend(frameon=False, loc="upper left", labelcolor=INK2)
    ax.set_xlabel("Approval year", color=INK2)
    ax.set_xlim(p.year.min() - 0.3, p.year.max() + 1.8)
    fig.savefig(OUT / fname, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def chart_effects(ame: pd.DataFrame):
    """Dot + 90% interval per scheme type and shock, one shared pp axis."""
    fig, ax = plt.subplots(figsize=(8, 4.4))
    schemes = list(dict.fromkeys(ame.scheme))
    for (shock, _, _, color), dy in zip(SHOCKS, [-0.22, 0, 0.22]):
        d = ame[ame.shock == shock].set_index("scheme").loc[schemes]
        y = np.arange(len(schemes)) + dy
        ax.hlines(y, d.ci_low, d.ci_high, color=color, linewidth=2)
        ax.plot(d.effect_pp, y, "o", color=color, markersize=8, label=shock)
        for yy, v, hi in zip(y, d.effect_pp, d.ci_high):
            ax.text(hi + 0.12, yy, f"{v:+.1f}pp", va="center", color=INK, fontsize=8.5)
    ax.set_yticks(range(len(schemes)), schemes)
    ax.axvline(0, color=INK2, linewidth=0.8)
    style(ax, "Change in chance of lapsing, by tenure (dots = estimate, lines = 90% interval)", "")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Percentage points", color=INK2)
    ax.set_ylim(len(schemes) - 0.5, -0.5)
    ax.set_xlim(right=ame.ci_high.max() + 1.2)
    ax.legend(frameon=False, loc="upper left", bbox_to_anchor=(0, -0.18), ncol=3, labelcolor=INK2)
    fig.savefig(OUT / "4_effects.png", dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------- model ----------
def fit(p):
    groups = pd.factorize(p.borough_key)[0]
    return smf.logit(FORMULA, p).fit(disp=0, cov_type="cluster", cov_kwds={"groups": groups})


def validate(p) -> dict:
    """Out-of-sample checks. Rates barely moved before 2020, so a train<=2019 model cannot learn the rate effect."""
    rng = np.random.default_rng(0)
    boroughs = p.borough_key.unique()
    fold = dict(zip(boroughs, rng.integers(0, 5, len(boroughs))))
    p = p.assign(fold=p.borough_key.map(fold), pred=np.nan)
    for k in range(5):
        m = smf.logit(FORMULA, p[p.fold != k]).fit(disp=0)
        p.loc[p.fold == k, "pred"] = m.predict(p[p.fold == k])
    calib = p.groupby(pd.qcut(p.pred, 5), observed=True).agg(pred=("pred", "mean"), actual=("lapsed", "mean"))
    train, test = p[p.year <= 2019], p[p.year >= 2020]
    oot = smf.logit(FORMULA, train).fit(disp=0).predict(test)
    return {
        "auc_borough_cv": round(roc_auc_score(p.lapsed, p.pred), 3),
        "calibration_quintiles": calib.round(3).to_dict(orient="records"),
        "auc_out_of_time_2020_22": round(roc_auc_score(test.lapsed, oot), 3),
        "out_of_time_pred_vs_actual": [round(oot.mean(), 3), round(test.lapsed.mean(), 3)],
    }


def bootstrap_coefs(p, reps=BOOT_REPS) -> pd.DataFrame:
    """Cluster bootstrap: resample whole boroughs with replacement, refit. Keeps within-borough correlation."""
    rng = np.random.default_rng(1)
    by_borough = {b: g for b, g in p.groupby("borough_key")}
    names = list(by_borough)
    draws = []
    for _ in range(reps):
        sample = pd.concat([by_borough[b] for b in rng.choice(names, len(names))], ignore_index=True)
        try:
            draws.append(smf.logit(FORMULA, sample).fit(disp=0).params)
        except Exception:  # rare separation in a resample; skip it
            continue
    return pd.DataFrame(draws)


def marginal_effects(p, coef_rows) -> pd.DataFrame:
    """Average change in P(lapse), in percentage points, for each shock in SHOCKS, for schemes set to a
    given sale share. Averaged over every scheme in the panel, so the other variables keep their real distribution."""
    rows = []
    for label, sale in [("0% for sale", 0.0), ("50% for sale", 0.5), ("100% for sale", 1.0)]:
        base = p.assign(sale=sale)
        for shock, col, delta, _ in SHOCKS:
            shocked = base.assign(**{col: base[col] + delta / p[SCALED[col]].std()})
            effs = [(predict(shocked, c) - predict(base, c)).mean() * 100 for c in coef_rows]
            rows.append({"scheme": label, "shock": shock, "effect_pp": effs[0],
                         "ci_low": np.percentile(effs[1:], 5), "ci_high": np.percentile(effs[1:], 95)})
    return pd.DataFrame(rows).round(2)


def decomposition(p, coef_rows) -> pd.DataFrame:
    """Counterfactual for the 2020-22 cohorts: predicted lapse if each market driver had stayed at its 2014-19 mean."""
    pre, post = p[p.year <= 2019], p[p.year >= 2020]
    reset = {name: (z, (pre[raw].mean() - p[raw].mean()) / p[raw].std()) for name, (z, raw) in MARKET.items()}
    scenarios = {"Actual market": post}
    scenarios.update({f"{name.capitalize()} at 2014-19 level": post.assign(**{z: v}) for name, (z, v) in reset.items()})
    scenarios["All at 2014-19 level"] = post.assign(**{z: v for z, v in reset.values()})
    preds = {k: np.array([predict(d, c).mean() for c in coef_rows]) * 100 for k, d in scenarios.items()}
    rise = preds["Actual market"] - preds["All at 2014-19 level"]
    rows = [{"scenario": k, "lapse_pct": v[0], "ci_low": np.percentile(v[1:], 5), "ci_high": np.percentile(v[1:], 95)}
            for k, v in preds.items()]
    for name in MARKET:
        share = (preds["Actual market"] - preds[f"{name.capitalize()} at 2014-19 level"]) / rise * 100
        rows.append({"scenario": f"Share of rise removed: {name}", "lapse_pct": share[0],
                     "ci_low": np.percentile(share[1:], 5), "ci_high": np.percentile(share[1:], 95)})
    rows.append({"scenario": "Observed 2020-22", "lapse_pct": post.lapsed.mean() * 100})
    rows.append({"scenario": "Observed 2014-19", "lapse_pct": pre.lapsed.mean() * 100})
    return pd.DataFrame(rows).round(1)


def predict(d, c) -> np.ndarray:
    x = (c["Intercept"] + c["sale"] * d.sale + c["sale_missing"] * d.sale_missing + c["z_units"] * d.z_units
         + c["tall"] * d.tall + c["storeys_missing"] * d.storeys_missing + c["z_hpi"] * d.z_hpi
         + c["z_rate"] * d.z_rate + c["z_rate:sale"] * d.z_rate * d.sale
         + c["z_cost"] * d.z_cost + c["z_cost:z_hpi"] * d.z_cost * d.z_hpi
         + c["z_sales"] * d.z_sales + c["z_sales:sale"] * d.z_sales * d.sale)
    return 1 / (1 + np.exp(-x))


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    p = load()
    scaling = standardise(p)

    chart_trend(p)
    known = p.sale_missing == 0
    chart_split(p, [known & (p.sale_share == 1), known & (p.sale_share < 0.5)],
                ["100% for sale", "Mostly affordable (<50% sale)"],
                "Lapse rate by tenure: affordable-led schemes caught up after 2020", "2_lapse_by_tenure.png")
    # Terciles within each approval year: prices trend up, so pooled cut-offs would sort by year.
    price_rank = p.groupby("year").hpi_price.rank(pct=True)
    chart_split(p, [price_rank <= 1 / 3, price_rank > 2 / 3],
                ["Cheapest third of boroughs", "Most expensive third"],
                "Lapse rate by local house prices (terciles within each year)", "3_lapse_by_price.png")

    m = fit(p)
    draws = bootstrap_coefs(p)
    ame = marginal_effects(p, [m.params] + [r for _, r in draws.iterrows()])
    ame.to_csv(OUT / "marginal_effects.csv", index=False)
    chart_effects(ame)
    decomposition(p, [m.params] + [r for _, r in draws.iterrows()]).to_csv(OUT / "decomposition.csv", index=False)
    results = {
        "formula": FORMULA,
        "n": int(m.nobs),
        "pseudo_r2": round(m.prsquared, 3),
        "coef": m.params.round(4).to_dict(),
        "p_value": m.pvalues.round(4).to_dict(),
        "scaling": scaling,
        "baseline": {"sale": float(p.sale.median()), "units": float(p.res_total_no_proposed_residential_units.median()),
                     "hpi_price": float(p.hpi_price.median()), "mortgage_rate_window": float(p.mortgage_rate_window.median()),
                     "build_cost_change": float(p.build_cost_change.median()),
                     "sales_index_window": float(p.sales_index_window.median())},
        "validation": validate(p),
        "coef_draws": draws.round(4).to_dict(orient="records"),  # cluster-bootstrap draws for uncertainty bands
    }
    (OUT / "model_results.json").write_text(json.dumps(results, indent=1, default=float))
    print(m.summary2().tables[1][["Coef.", "P>|z|"]].round(3).to_string())
    print(json.dumps(results["validation"], indent=1))
    print(f"bootstrap draws: {len(draws)}\n", ame.to_string(index=False))

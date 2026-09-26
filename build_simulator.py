"""Bundle the fitted model + the live pipeline into a self-contained simulator page.

Inputs:  outputs/model_results.json (analysis.py), data/* (pipeline.py inputs)
Output:  outputs/simulator.html  (simulator_template.html with the data inlined)
Run: .venv/bin/python build_simulator.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline import WINDOW, add_scheme_features, load_build_costs, load_hpi, load_mortgage, load_sales_index, load_schemes

ROOT = Path(__file__).parent
PIPELINE_FROM = "2023-01"  # approved since 2023 and still not started = the live pipeline


def main():
    model = json.loads((ROOT / "outputs" / "model_results.json").read_text())
    hpi = load_hpi()
    latest = hpi[hpi.month == hpi.month.max()].set_index("borough_key").AveragePrice

    live = load_schemes()
    live = live[(live.approval_month >= pd.Period(PIPELINE_FROM)) & (live.status == "Approved")]
    live = add_scheme_features(live)
    live = live[live.borough_key.isin(latest.index)]
    sale_fill = model["baseline"]["sale"]
    pipeline = [
        [int(r.res_total_no_proposed_residential_units),
         float(sale_fill if np.isnan(r.sale_share) else r.sale_share), int(np.isnan(r.sale_share)),
         int(r.tall), int(r.storeys_missing), round(float(np.log(latest[r.borough_key])), 4)]
        for r in live.itertuples()
    ]

    rate, cost = load_mortgage(), load_build_costs()
    last_cost = cost.index.max()
    latest_rate, latest_cost = float(rate.dropna().iloc[-1]), float(cost[last_cost] / cost[last_cost - WINDOW] - 1)
    # Rounded to the sliders' steps so the "Today" preset reproduces exactly; exact values shown as hints.
    sales = load_sales_index()
    latest_sales = float(sales.iloc[-12:].mean().mean())  # last 12 complete months, averaged over boroughs
    today = {"rate": round(latest_rate, 1), "cost": round(latest_cost, 2), "sales": round(latest_sales, 2),
             "rate_exact": round(latest_rate, 2), "cost_exact": round(latest_cost, 3)}

    data = {
        "coef": model["coef"], "draws": model["coef_draws"], "scaling": model["scaling"], "n": model["n"],
        "auc": model["validation"]["auc_borough_cv"],
        "today": today, "rate_month": str(rate.dropna().index[-1]), "cost_month": str(last_cost), "sales_month": str(sales.index[-1]),
        "hpi_month": str(hpi.month.max()),
        "boroughs": {k.title().replace(" And ", " and ").replace(" Upon ", " upon ").replace(" Of ", " of "): round(v)
                     for k, v in latest.sort_index().items()},
        "pipeline": pipeline,
    }
    html = (ROOT / "simulator_template.html").read_text().replace("__SIM_DATA__", json.dumps(data))
    (ROOT / "outputs" / "simulator.html").write_text(html)
    print(f"pipeline: {len(pipeline)} schemes, {sum(p[0] for p in pipeline):,} homes | today: {today}")


if __name__ == "__main__":
    main()

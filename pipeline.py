"""Build the scheme-level panel: one row per approved 10+ unit scheme, with outcome and the core drivers.

Inputs (all in data/, see PLAN.md):
  pld_schemes.parquet            pld_download.py
  mortgage_rates_monthly.csv     boe_download.py
  uk_hpi_full.csv                https://publicdata.landregistry.gov.uk/market-trend-data/house-price-index-data/UK-HPI-full-file-2026-06.csv
  ons_opi.xlsx                   ONS Construction Output Price Indices (bulletindataset9.xlsx)
Output: data/scheme_panel.parquet
Run: .venv/bin/python pipeline.py
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).parent / "data"
WINDOW = 36  # months: the permission lapses if not started within 3 years
FIRST, LAST = "2014-01", "2022-12"  # ONS index starts 2014; 2023+ cohorts have not reached their deadline
TALL_STOREYS = 7
SALES_BASELINE = ("2014-01", "2019-12")  # "normal" local sales volume each borough is compared against

HPI_NAME_FIXES = {"westminster": "city of westminster", "richmond": "richmond upon thames",
                  "kingston": "kingston upon thames"}


def norm_borough(name) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    n = re.sub(r"^london borough of |^royal borough of ", "", name.strip().lower()).replace("&", "and")
    n = re.sub(r"\s*(\(la code\)|custodian code|council)$", "", n).strip()
    return HPI_NAME_FIXES.get(n, n)


# ---------- market tables (monthly) ----------
def load_mortgage() -> pd.Series:
    m = pd.read_csv(DATA / "mortgage_rates_monthly.csv", index_col="month")
    m.index = pd.PeriodIndex(m.index, freq="M")
    return m["mortgage_2y_75ltv"]


def load_build_costs() -> pd.Series:
    """ONS new-work housing output price index (2015=100), monthly from Jan 2014."""
    raw = pd.read_excel(DATA / "ons_opi.xlsx", sheet_name="New work", header=None, skiprows=5).iloc[:, :2]
    raw.columns = ["period", "index"]
    # Period column is "2014 Jan", then "Feb", "Mar"... with the year only on January.
    year, months = None, []
    for p in raw["period"].astype(str):
        parts = p.split()
        if len(parts) == 2:
            year = parts[0]
        months.append(pd.Period(f"{year} {parts[-1]}", freq="M"))
    return pd.Series(raw["index"].astype(float).values, index=pd.PeriodIndex(months), name="build_cost_index")


def load_hpi() -> pd.DataFrame:
    h = pd.read_csv(DATA / "uk_hpi_full.csv", usecols=["Date", "RegionName", "AreaCode", "AveragePrice", "SalesVolume"])
    h = h[h.AreaCode.str.startswith("E09")].copy()
    h["month"] = pd.to_datetime(h.Date, format="%d/%m/%Y").dt.to_period("M")
    h["borough_key"] = h.RegionName.map(norm_borough)
    return h[["borough_key", "AreaCode", "month", "AveragePrice", "SalesVolume"]]


def load_sales_index() -> pd.DataFrame:
    """Monthly home sales per borough divided by that borough's 2014-19 monthly average (1 = normal demand).
    Land Registry registrations lag by months, so the latest months are incomplete: keep up to Dec 2025."""
    h = load_hpi()
    sales = h.pivot_table(index="month", columns="AreaCode", values="SalesVolume").loc[:"2025-12"]
    return sales / sales.loc[SALES_BASELINE[0]:SALES_BASELINE[1]].mean()


def window_mean(series: pd.Series, start: pd.Period, months: int) -> float:
    return series.loc[start: start + (months - 1)].mean()


# ---------- scheme panel ----------
def max_storeys(building_details) -> float:
    if not isinstance(building_details, str):
        return np.nan
    storeys = [b.get("no_storeys") or 0 for b in json.loads(building_details)]
    return max(storeys) if storeys and max(storeys) > 0 else np.nan


def load_schemes() -> pd.DataFrame:
    df = pd.read_parquet(DATA / "pld_schemes.parquet")
    df["decision_date"] = pd.to_datetime(df.decision_date, format="%d/%m/%Y", errors="coerce")
    df["approval_month"] = df.decision_date.dt.to_period("M")
    return df


def build_panel() -> tuple[pd.DataFrame, dict]:
    df = load_schemes()
    audit = {"approved_10plus": len(df)}
    df = df[df.approval_month.between(pd.Period(FIRST), pd.Period(LAST))]
    audit["in_2014_2022"] = len(df)

    # Outcome. Superseded = replaced by a later permission (not a failure); other statuses are unresolved.
    df = df[df.status.isin(["Completed", "Commenced", "Lapsed"])].copy()
    audit["resolved_outcome"] = len(df)
    df["lapsed"] = (df.status == "Lapsed").astype(int)
    return add_scheme_features(df), audit


def add_scheme_features(df: pd.DataFrame) -> pd.DataFrame:
    """Drivers 1-3 (PLAN.md §2) plus the borough join key. Shared by the panel and the live pipeline."""
    df = df.copy()
    units = df.res_total_no_proposed_residential_units
    tenure_cols = [c for c in df if c.startswith("res_total_no_proposed_residential_units_")]
    tenure_known = df[tenure_cols].sum(axis=1) >= 0.9 * units
    df["sale_share"] = np.where(tenure_known, df.res_total_no_proposed_residential_units_market_for_sale / units, np.nan)
    df["log_units"] = np.log(units)
    df["storeys"] = df.ad_building_details.map(max_storeys)
    df["tall"] = (df.storeys >= TALL_STOREYS).astype(int)
    df["storeys_missing"] = df.storeys.isna().astype(int)

    # PLD `borough` holds the host borough even for LLDC/OPDC applications; fall back to the LPA
    # when it is not a recognisable borough.
    valid = set(load_hpi().borough_key)
    from_borough = df.borough.map(norm_borough)
    df["borough_key"] = from_borough.where(from_borough.isin(valid), df.lpa_name.map(norm_borough))
    return df


def attach_market(df: pd.DataFrame) -> pd.DataFrame:
    rate, cost, hpi, sales = load_mortgage(), load_build_costs(), load_hpi(), load_sales_index()
    df = df.merge(hpi.drop(columns="SalesVolume").rename(columns={"month": "approval_month", "AveragePrice": "hpi_price"}),
                  on=["borough_key", "approval_month"], how="left")
    # Demand: local home sales over the same window, relative to the borough's normal level.
    df["sales_index_window"] = [window_mean(sales[a], m, WINDOW) if a in sales else np.nan
                                for m, a in zip(df.approval_month, df.AreaCode)]
    df["mortgage_rate_window"] = df.approval_month.map(lambda m: window_mean(rate, m, WINDOW))
    end = df.approval_month + WINDOW
    df["build_cost_change"] = end.map(cost).values / df.approval_month.map(cost).values - 1
    return df


COLUMNS = ["id", "lpa_app_no", "lpa_name", "borough_key", "AreaCode", "approval_month", "status", "lapsed",
           "res_total_no_proposed_residential_units", "log_units", "sale_share", "storeys", "tall",
           "storeys_missing", "hpi_price", "mortgage_rate_window", "build_cost_change", "sales_index_window",
           "centroid.lat", "centroid.lon", "description"]

if __name__ == "__main__":
    panel, audit = build_panel()
    panel = attach_market(panel)[COLUMNS]
    audit["unmatched_borough"] = int(panel.AreaCode.isna().sum())
    audit["missing_sale_share"] = int(panel.sale_share.isna().sum())
    panel["approval_month"] = panel.approval_month.astype(str)
    panel.to_parquet(DATA / "scheme_panel.parquet", index=False)
    print(json.dumps(audit, indent=1))
    print(panel.describe().T.round(3).to_string())

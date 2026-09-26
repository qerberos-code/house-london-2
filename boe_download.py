"""Download Bank of England mortgage-rate series into one monthly table.

Output: data/mortgage_rates_monthly.csv, one row per month:
  month                 YYYY-MM
  mortgage_2y_75ltv     IUMBV34 — quoted 2-year fixed mortgage rate, 75% LTV (%). Core variable.
  mortgage_2y_95ltv     IUMB482 — quoted 2-year fixed, 95% LTV (%). First-time buyers; robustness only.
  sonia                 IUDSOIA — SONIA, monthly mean of daily values (%). Developer finance proxy; robustness only.
Run: .venv/bin/python boe_download.py
"""
import io
from pathlib import Path

import pandas as pd
import requests

URL = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
SERIES = {"IUMBV34": "mortgage_2y_75ltv", "IUMB482": "mortgage_2y_95ltv", "IUDSOIA": "sonia"}
START = "01/Jan/2010"
OUT = Path(__file__).parent / "data" / "mortgage_rates_monthly.csv"


def fetch(code: str) -> pd.Series:
    params = {"csv.x": "yes", "Datefrom": START, "Dateto": "now", "SeriesCodes": code, "CSVF": "TN", "UsingCodes": "Y"}
    # The BoE endpoint rejects requests without a browser-like User-Agent.
    r = requests.get(URL, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    dates = pd.to_datetime(df["DATE"], format="%d %b %Y")
    return pd.Series(df[code].values, index=dates.dt.to_period("M"), name=SERIES[code]).groupby(level=0).mean()


if __name__ == "__main__":
    out = pd.concat([fetch(c) for c in SERIES], axis=1).sort_index().round(4)
    out.index = out.index.astype(str)
    out.index.name = "month"
    OUT.parent.mkdir(exist_ok=True)
    out.to_csv(OUT)
    print(out.describe().round(2), f"\nsaved {out.shape} -> {OUT}", sep="\n")

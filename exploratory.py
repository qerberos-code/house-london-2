# %% [markdown]
# # Foundations — exploratory analysis
# London planning applications (planit.org.uk) scored > 0.9 housing relevance.
# Source: https://foreman.house-london.uk  (API docs: /api/docs/)
# Run cell-by-cell in VS Code (# %%) or `python exploratory.py`.

# %% Setup
from pathlib import Path

import numpy as np
import pandas as pd
import requests

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 50)
pd.set_option("display.max_rows", 200)

BASE = "https://foreman.house-london.uk"
DATA = Path(__file__).parent / "data"
PARQUET = DATA / "foundations.parquet"

DATE_COLS = ["start_date", "decided_date", "target_decision_date"]
DECIDED = ["Permitted", "Conditions", "Rejected"]  # clean binary outcome; drops Withdrawn/Undecided/...


# %% Loading
def download_parquet(force: bool = False) -> Path:
    """Bulk dump (~25MB). Always the default >0.9 view: the download ignores min_relevance_score."""
    if force or not PARQUET.exists():
        DATA.mkdir(exist_ok=True)
        r = requests.get(f"{BASE}/download/parquet/", timeout=300)
        r.raise_for_status()
        PARQUET.write_bytes(r.content)
    return PARQUET


def fetch_api(max_pages: int | None = None, **params) -> pd.DataFrame:
    """Paginated API (fixed 100 rows/page). Use for filtered slices or scores below the 0.9 default,
    e.g. fetch_api(min_relevance_score=0.5, area_name="Southwark")."""
    url, rows, page = f"{BASE}/api/v1/applications/", [], 0
    while url and (max_pages is None or page < max_pages):
        r = requests.get(url, params=params if page == 0 else None, timeout=60)
        r.raise_for_status()
        body = r.json()
        rows += body["results"]
        url, page = body["next"], page + 1
    return pd.DataFrame(rows)


def clean(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    for c in DATE_COLS:
        df[c] = pd.to_datetime(df[c], errors="coerce")

    # Placeholders -> NaN. case_officer is 100% "See source" or empty: no signal, drop it.
    # reference is 99% empty (uid carries the same info).
    for c in ["app_type", "app_size", "status", "ward_name", "agent_company", "decided_by", "decision"]:
        df[c] = df[c].replace(r"^\s*$", np.nan, regex=True)
    df = df.drop(columns=["case_officer", "reference"])

    # Coordinates: a handful geocoded outside Greater London.
    out = ~df.lat.between(51.25, 51.72) | ~df.lng.between(-0.55, 0.35)
    df.loc[out, ["lat", "lng"]] = np.nan

    # Negative days_to_decision are data errors (decided before registered).
    df.loc[df.days_to_decision < 0, "days_to_decision"] = np.nan
    return df.set_index("name")


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["decided"] = df.status.isin(DECIDED)
    df["approved"] = np.where(df.decided, (df.status != "Rejected").astype(float), np.nan)
    df["start_year"] = df.start_date.dt.year
    df["start_month"] = df.start_date.dt.to_period("M")

    # Statutory clock: 56 days (8wk) Small, 91 (13wk) Medium/Large. target_decision_date is 30% null,
    # so fall back to the nominal clock.
    nominal = np.where(df.app_size == "Small", 56, 91)
    df["target_days"] = (df.target_decision_date - df.start_date).dt.days.fillna(pd.Series(nominal, index=df.index))
    df["days_vs_target"] = df.days_to_decision - df.target_days
    df["on_deadline_day"] = df.days_vs_target == 0
    df["late"] = df.days_vs_target > 0

    # Decision route: collapse 100+ free-text values.
    by = df.decided_by.str.lower()
    df["decided_by_cat"] = np.select(
        [by.str.contains("committee|panel|member", na=False), by.str.contains("deleg", na=False),
         by.str.contains("withdraw", na=False)],
        ["committee", "delegated", "withdrawn"], default=None)

    df["has_agent"] = df.agent_company.notna()
    df["desc_len"] = df.description.str.len()

    # Description-derived proposal type. Outline in this data is mostly prior approvals / lawful
    # certificates, not true outline permissions, so text is a better "what is it" signal than app_type.
    low = df.description.str.lower()
    kw = {
        "kw_hmo": r"\bhmo\b|house in multiple",
        "kw_change_of_use": r"change of use",
        "kw_prior_approval": r"prior approval",
        "kw_lawful_cert": r"lawful",
        "kw_new_dwelling": r"erection of .*dwelling|new dwelling",
        "kw_flats": r"\bflats?\b|self-contained",
        "kw_dormer_loft": r"dormer|loft",
        "kw_basement": r"basement",
        "kw_demolition": r"demoli",
        "kw_outbuilding": r"outbuilding",
    }
    for col, pat in kw.items():
        df[col] = low.str.contains(pat, regex=True, na=False)
    return df


# %% Load
raw = pd.read_parquet(download_parquet())
df = add_features(clean(raw))
dec = df[df.decided]
print(df.shape, "| decided:", len(dec), "| approval:", round(dec.approved.mean(), 3))


# %% Missingness (null or placeholder)
def missing_report(raw: pd.DataFrame) -> pd.DataFrame:
    s = raw.astype("string").fillna("").apply(lambda c: c.str.strip().str.lower())
    return pd.DataFrame({
        "missing_%": s.isin(["", "see source"]).mean().mul(100).round(1),
        "nunique": raw.nunique(),
    }).sort_values("missing_%", ascending=False)


print(missing_report(raw))

# Structural (by-borough) missingness: n_comments is all-or-nothing per borough -> can't be
# used as a London-wide feature without restricting to the 14 boroughs that publish it.
coverage = df.groupby("area_name")[["n_comments", "n_dwellings", "agent_company", "decided_by", "target_decision_date"]] \
    .agg(lambda s: s.notna().mean()).round(2)
print(coverage.sort_values("n_comments"))

# %% Outcomes by borough / type / size
print(dec.groupby("app_type").approved.agg(["mean", "size"]).round(3))
print(dec.groupby("app_size", dropna=False).approved.agg(["mean", "size"]).round(3))

borough = dec.groupby("area_name").agg(
    approval=("approved", "mean"), n=("approved", "size"),
    median_days=("days_to_decision", "median"),
    share_on_deadline=("on_deadline_day", "mean"), share_late=("late", "mean"),
).round(3).sort_values("approval")
print(borough)

# %% The deadline spike (replicates hackathon #0 "The Spike")
small = dec[dec.app_size == "Small"]
spike = small[small.days_vs_target.between(-7, 7)].groupby("days_vs_target").approved.agg(["mean", "size"]).round(3)
print(spike.T)

# %% Text & engagement signals
kw_cols = [c for c in df if c.startswith("kw_")]
print(pd.DataFrame({k: (dec[k].sum(), dec.loc[dec[k], "approved"].mean()) for k in kw_cols},
                   index=["n", "approval"]).T.sort_values("approval").round(3))

comm = dec[dec.n_comments.notna()]
print(comm.groupby(pd.cut(comm.n_comments, [-1, 0, 2, 10, np.inf]), observed=True).approved.agg(["mean", "size"]).round(3))
print(dec.groupby(pd.qcut(dec.n_documents, 5, duplicates="drop"), observed=True).approved.agg(["mean", "size"]).round(3))

# %% Time trend (right-censoring: recent years have more Undecided)
print(pd.crosstab(df.start_year, df.status, normalize="index").round(3))

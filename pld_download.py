"""Download approved 10+ unit residential schemes from the Planning London Datahub (GLA).

Guest Elasticsearch endpoint used by the PLD web app (public, may change).
Output: data/pld_schemes.parquet — one row per application, flattened.
Run: .venv/bin/python pld_download.py
"""
import json
from pathlib import Path

import pandas as pd
import requests

URL = "https://planningdata.london.gov.uk/api-guest/applications/_search"
HEADERS = {"Content-Type": "application/json", "X-API-AllowRequest": "be2rmRnt&"}
OUT = Path(__file__).parent / "data" / "pld_schemes.parquet"
RAW = OUT.with_suffix(".json")
MIN_UNITS = 10
PAGE = 1000

TOP_FIELDS = [
    "id", "lpa_name", "borough", "lpa_app_no", "description", "application_type", "application_type_full",
    "development_type", "status", "decision", "decision_date", "valid_date", "decision_process",
    "actual_commencement_date", "actual_completion_date", "lapsed_date", "centroid", "ward",
    "cil_liability", "reference_no_of_permission_being_relied_on", "last_updated",
]
AD = "application_details."
RD = AD + "residential_details."
RES_FIELDS = [
    "total_no_proposed_residential_units", "total_no_existing_residential_units",
    "total_no_proposed_residential_affordable_units", "affordable_percentage",
    "total_no_proposed_residential_units_market_for_sale", "total_no_proposed_residential_units_market_for_rent",
    "total_no_proposed_residential_units_social_rent", "total_no_proposed_residential_units_affordable_rent",
    "total_no_proposed_residential_units_london_affordable_rent", "total_no_proposed_residential_units_intermediate",
    "total_no_proposed_residential_units_london_shared_ownership", "total_no_proposed_residential_units_london_living_rent",
    "total_no_proposed_bedrooms", "dwelling_density", "site_area",
]
SOURCE = TOP_FIELDS + [RD + f for f in RES_FIELDS] + [
    AD + "building_details", AD + "superseding_details", AD + "site_area", AD + "scheme_name",
]


def fetch() -> list[dict]:
    query = {
        "bool": {
            "must": [
                {"range": {RD + "total_no_proposed_residential_units": {"gte": MIN_UNITS}}},
                {"match": {"decision": "Approved"}},
            ]
        }
    }
    rows, search_after = [], None
    while True:
        body = {"size": PAGE, "query": query, "_source": SOURCE, "sort": [{"_doc": "asc"}]}
        if search_after:
            body["search_after"] = search_after
        r = requests.post(URL, json=body, headers=HEADERS, timeout=120)
        r.raise_for_status()
        hits = r.json()["hits"]["hits"]
        if not hits:
            return rows
        rows += [h["_source"] for h in hits]
        search_after = hits[-1]["sort"]
        print(f"\r{len(rows)} rows", end="", flush=True)


def flatten(rows: list[dict]) -> pd.DataFrame:
    df = pd.json_normalize(rows, max_level=3)
    df.columns = [c.replace(RD, "res_").replace(AD, "ad_") for c in df.columns]
    # Nested lists/dicts (building_details, superseding_details) kept as JSON strings for parquet.
    # Other object columns mix str/float across boroughs: numeric where fully parseable, else str.
    for c in df.columns:
        if df[c].dtype != object and str(df[c].dtype) != "str":
            continue
        if df[c].map(lambda v: isinstance(v, (list, dict))).any():
            df[c] = df[c].map(lambda v: json.dumps(v) if isinstance(v, (list, dict)) else v)
        num = pd.to_numeric(df[c], errors="coerce")
        df[c] = num if num.notna().sum() == df[c].notna().sum() else df[c].astype("string")
    return df


if __name__ == "__main__":
    OUT.parent.mkdir(exist_ok=True)
    if not RAW.exists():
        RAW.write_text(json.dumps(fetch()))
    df = flatten(json.loads(RAW.read_text()))
    df.to_parquet(OUT, index=False)
    print(f"\nsaved {df.shape} -> {OUT}")

# Why do approved housing schemes never get built?

**Goal.** Find which variables explain whether an approved London housing scheme gets built or lapses,
then ship a simulator: change the inputs (mortgage rates, build costs, scheme mix) and see how the
probability of the scheme being built changes.

**Question behind it.** Is non-delivery mainly a *demand* problem (buyers priced out by lender
mortgage rates) or a *supply* problem (build costs and finance squeezing margins)?

This is the open question left by House London #0 ("why do schemes actually stall? — untested").

---

## 1. Data verdict (checked 26 Sep 2026)

| Need | Source | Status |
|---|---|---|
| Outcome: built / lapsed | Planning London Datahub (PLD): `status`, `actual_commencement_date`, `actual_completion_date` | ✅ 22,520 approved 10+ unit schemes, 35 authorities, 2012–2025 (`pld_download.py`) |
| Scheme size | PLD `res_total_no_proposed_residential_units` | ✅ 100% |
| Tenure mix (sale vs affordable) | PLD `res_*_market_for_sale`, `res_affordable_percentage` | ✅ 70–83% of schemes per year (2012+) |
| Height | PLD `ad_building_details.no_storeys` | ⚠️ ~55% since 2014 → use with a "missing" flag |
| Lender mortgage rates | Bank of England IADB `IUMBV34` (2y fixed 75% LTV), plus `IUMB482` (95% LTV) and `IUDSOIA` (SONIA) for robustness | ✅ **done**: `boe_download.py` → `data/mortgage_rates_monthly.csv`, Jan 2010 – Aug 2026 |
| Build costs | ONS Construction Output Price Indices (`bulletindataset9.xlsx`) | ✅ quarterly |
| Local prices / sales volumes | UK HPI full file (Land Registry) | ✅ monthly, by borough |
| Follow-on applications | Foundations (House London) | ⚠️ only 2022–2025, overlaps the censored cohorts. Validation only |

**What we do NOT have (state it in the pitch):**
- land prices;
- actual development-loan terms;
- scheme viability assessments;
- sales absorption per scheme;
- developer identity.

We use proxies, not measurements.

**Data traps found:**
- `lapsed_date` is filled for ~100% of rows. It is the *expiry date*, not an event. Use `status == "Lapsed"` as the outcome.
- `status == "Superseded"` (~22%) means the scheme was replaced by a later permission (s73 or re-plan). It is not a failure. Drop it, or follow the chain through `ad_superseding_details`.
- 2023+ cohorts have not reached their 3-year deadline. They are censored, so never read them as "less lapsing".
- `market_for_rent` (Build to Rent) is coded in only ~2% of schemes, so it is unreliable. Use the **market-for-sale share** instead.
- A recorded commencement can be a token start to beat the 3-year lapse. Check by scheme size.

**Early signal.** Share of schemes that lapsed, among completed + commenced + lapsed, by approval year:

| Approval years | Lapsed |
|---|---|
| 2014–2019 | ~15% |
| 2020 | 23% |
| 2021 | 32% |
| 2022 | 33% |

The 2021–22 cohorts hit their 3-year deadline in 2024–25, when rates were high. Their lapse rate doubled.

---

## 2. Core variables (agreed: 6)

One outcome and six drivers:
- **three come from the PLD itself** and need no merge;
- **three are external series**, each a simple date join.

| # | Variable | Role | Source | Merge |
|---|---|---|---|---|
| Y | **Lapsed within 3 years** (vs started) | Outcome | PLD `status` | — |
| 1 | Tall building (≥7 storeys, + missing flag) | Regulation (Gateway 2) and cost | PLD `ad_building_details` | none |
| 2 | **Market-for-sale share** | Demand exposure: sale schemes need buyers | PLD `res_*_market_for_sale` / total units | none |
| 3 | Units (log) | Size control | PLD `res_total_no_proposed_residential_units` | none |
| 4 | House prices (HPI) at approval | Margin headroom: high prices absorb cost rises | UK HPI (gov.uk) | borough + month |
| 5 | **2y fixed mortgage rate**, mean over the 3-year window after approval | Demand (price of credit) | BoE `IUMBV34` → `data/mortgage_rates_monthly.csv` | month |
| 6 | **Build-cost inflation** from approval to end of window | Supply cost | ONS Construction Output Price Indices | quarter |

**Added later:** local home sales per borough (UK HPI `SalesVolume`, relative to each borough's 2014–19 average) as a direct demand measure. See REPORT.md §4.

**Dropped on purpose:**
- **Affordable %**: roughly the inverse of the sale share, so it is redundant.
- **Mortgage approvals**: moves with the mortgage rate.
- **SONIA and the 95% LTV rate**: already in the mortgage file, used for robustness checks only.

**The key test (demand vs supply).** Interactions, not just levels:
- `mortgage_rate × sale_share`: if rates hurt **for-sale** schemes more, the problem is **demand**.
- `cost_inflation × low_HPI`: if costs hurt **low-price** areas more, the problem is **supply** (margins).
- The height flag separates the **regulation** effect (Gateway 2).

Without the sale share, rates and costs cannot be told apart, because both rose in 2022–24.

---

## 3. Process

1. **Build the scheme panel.** One row per scheme, from 2012 to 2022 so every cohort has had its full 3-year window.
   - Clean `status` and drop superseded schemes.
   - Attach the market variables, averaged over each scheme's 3-year window.
   - Output: `data/scheme_panel.parquet` plus a data dictionary. This is the **reusable asset** for future editions.
2. **Describe.** Plot the lapse rate by cohort, by sale share and by borough price, alongside the time series of rates and costs. This shows whether the story holds before any modelling.
3. **Model v1.** Logistic regression `P(lapse)` on the 6 drivers plus the 2 interactions. It is interpretable and its coefficients are what the simulator uses. Validate on held-out cohorts (train ≤2019, test 2020–22) and check calibration.
4. **Model v2 (if time).** A discrete-time hazard on a scheme × quarter panel, which handles censoring and lets 2023+ cohorts in. Only worth doing if v1 is solid.
5. **Simulator.** Sliders for mortgage rate, build-cost inflation, sale share, HPI and height:
   - probability of the scheme being built;
   - homes unlocked across the current pipeline (approved, not started).
6. **Story and pitch.** The demand-vs-supply verdict, the simulator demo and the limits.

## 4. Team split

| Who | Task | Output | Status |
|---|---|---|---|
| **PLD pair** | Scheme panel: one row per scheme, 2012–2022, outcome Y, drop `Superseded`, variables 1–3, borough, approval date | `scheme_panel` | In progress. `pld_download.py` already downloads the 22,520 approved 10+ unit schemes, so reuse it or compare with it |
| **HPI + costs** | UK HPI by borough-month + ONS build-cost index by quarter | `hpi_costs` table | In progress |
| **Mortgage** | BoE series by month | `data/mortgage_rates_monthly.csv` | ✅ Done |
| **Merge** | Join the tables onto the panel. Per scheme: HPI at approval month; mean mortgage rate and cost change over [approval, approval + 36 months] | `scheme_panel_final` | Next |
| **Model + simulator** | Descriptive charts → logistic v1 with the 2 interactions → simulator sliders | Verdict, app, slides | After merge |

**Join keys to agree on now:**
- **Borough names**: PLD says "Barking & Dagenham", HPI says "Barking and Dagenham". Build one lookup table using ONS borough codes (E09…).
- **Dates**: approval date as `YYYY-MM` month; build costs map month → quarter.

## 5. Checkpoints

1. The panel and market table are joined, with no broken rows.
2. The descriptive charts confirm or kill the early signal, **before** any modelling.
3. Model v1 has sensible signs, calibration is OK and the demand-vs-supply test is run.
4. The simulator is wired to the real coefficients.
5. Pitch freeze.

---

## 6. Status and first results (26 Sep 2026)

> Full write-up with method, robustness and interpretation: **[REPORT.md](REPORT.md)**. It supersedes the summary below where they differ.

**Reference pipeline, built end to end. Run in this order:**

```bash
.venv/bin/python pld_download.py     # PLD approved 10+ unit schemes  -> data/pld_schemes.parquet
.venv/bin/python boe_download.py     # BoE mortgage rates             -> data/mortgage_rates_monthly.csv
# UK HPI full file -> data/uk_hpi_full.csv ; ONS OPI bulletindataset9.xlsx -> data/ons_opi.xlsx (URLs in pipeline.py)
.venv/bin/python pipeline.py         # scheme panel + merges          -> data/scheme_panel.parquet
.venv/bin/python analysis.py         # charts + model + bootstrap     -> outputs/*.png, model_results.json, marginal_effects.csv, decomposition.csv
.venv/bin/python robustness.py       # sensitivity table              -> outputs/robustness.csv
.venv/bin/python build_simulator.py  # simulator page                 -> outputs/simulator.html
```

**Panel.**
- 4,259 schemes approved 2014–2022 with a resolved outcome (Completed, Commenced or Lapsed).
- Superseded schemes are dropped.
- 6 schemes approved by the LLDC development corporation have no borough match and drop out of the model.

**Results** (logit, SEs clustered by borough; robust to adding a time trend or borough fixed effects):

| Driver | Effect on P(lapse) | Verdict |
|---|---|---|
| Mortgage rate over the window | **Strong ↑**, the dominant driver: lapse rose from 15% (2014 cohort) to 33% (2022 cohort) | Credit cost matters most |
| Rate × sale share | **Negative**: rates hurt *affordable-led* schemes more than for-sale ones | Not pure buyer demand. Points to a **financing and viability** channel: developer and housing-association borrowing costs |
| For-sale share | ↑ at every rate level | For-sale schemes are always riskier |
| Build-cost inflation | Small ↑, not robust once rates and a trend are included | Secondary |
| Cost × low prices | Right sign, not significant | Weak evidence for a margin squeeze |
| House-price level, size | No clear effect | — |
| Tall building | Small ↑, p≈0.1 | Weak Gateway 2 signal |

**Validation.**
- Borough-grouped cross-validation AUC is 0.63 and calibration by quintile is good. The model explains average patterns, not individual sites.
- A model trained only on ≤2019 cohorts cannot learn the rate effect (AUC 0.53), because rates barely moved before 2020. The rate effect is identified by the 2020–22 cohorts alone, so it is effectively one natural experiment.

**Simulator** (`outputs/simulator.html`). The live pipeline is 581 schemes and 111,479 homes approved since 2023 and not yet started. Expected homes lapsing:

| Scenario | Homes lapsing | Share |
|---|---|---|
| 2015–19 conditions | ~11k | 10% |
| Today (4.9% rate) | ~31k | 28% |
| 2022–23 shock (4.8% rate, +25% costs) | ~38k | 34% |

**Open follow-ups:**
- Compare against the teammates' PLD, HPI and cost tables.
- Swap SONIA for the mortgage rate as a robustness check (finance vs demand).
- Add a survival model so 2023+ cohorts can enter.

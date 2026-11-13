# E-Presso BI Analyst

Team: Fatima, Aon, Nellie

## What this application does
This app lets an E-Presso manager explore five years of sales (2025–2029) across the website and 20 physical stores. After the five E-Presso data files are uploaded, it checks them for problems, joins them safely, and shows total revenue, website share, growth, and four charts that respond to year, channel, category and store filters. A built-in assistant answers business questions in plain English, such as "Which store declined the most?". Every number it gives comes from one of six tested calculations run on the uploaded data, and it says plainly when the data cannot answer a question, such as anything about profit. Every figure is recalculated from whatever files are uploaded, so an updated dataset works without any code change.

## Live app
**https://epreapp-bi-analyst-9xtb2kgmfn8rb95kn7luog.streamlit.app/**

The app sleeps when unused. If it shows a wake-up button, click it and allow about a minute.

## How to use it
1. **Upload the five files**, one per box in the sidebar (CSV or Excel):

   | Box | File | Notes |
   |---|---|---|
   | Sales | `Sales.csv` | Exported from the `Sales` table in Support_EX_B_Sales.accdb |
   | Stores | `Support_EX_B_Stores.csv` | Tab-delimited; read as supplied |
   | Products | `Products.csv` | Exported from the `Products` table in the .accdb |
   | Categories | `Categories.csv` | Exported from the `Categories` table in the .accdb |
   | Survey | `Support_EX_B_Survey.xlsx` | The `Survey Results` sheet is picked automatically; its header is on row 3 |

   Until all five are loaded, the page lists the missing tables. If a file lacks a required column, the app names the column.
2. **Data and profile tab:** row counts, missing values, duplicate keys, data types, and data-quality checks for each table.
3. **Data model tab:** grain statements and the merge checks (rows and revenue before and after every join).
4. **Dashboard tab:** choose filters (leave one empty to include everything). The Scope line, headline numbers and all four charts update together.
5. **Assistant tab:** type a business question. Choose **Gemini** (Option A) or **Rules-based** (Option B). Each answer shows who answered it, its scope, Findings, Interpretation, Next steps, and a tool-call log. Limit: 15 questions per session.

## Data model

### Grain statements
| Table | Source file / sheet | One row = | Key column(s) | Row count | Contains revenue? |
|---|---|---|---|---|---|
| Sales | `Sales` table in Support_EX_B_Sales.accdb, exported to `Sales.csv` | one product at one store (or the website) in one month | Sales ID | 25,200 | Yes (`Revenue`) |
| Stores | Support_EX_B_Stores.csv (tab-delimited) | one store; SH00 is the website | ID | 21 | No |
| Products | `Products` table in Support_EX_B_Sales.accdb, exported to `Products.csv` | one product | Product ID | 20 | No |
| Categories | `Categories` table in Support_EX_B_Sales.accdb, exported to `Categories.csv` | one product category | Category ID | 5 | No |
| Survey | Support_EX_B_Survey.xlsx, sheet `Survey Results` (header on row 3) | one store in one year | Survey ID (= StoreID_YY) | 105 | No |

Row counts are for the original files; the app shows live counts for whatever is uploaded.
Check: 21 stores × 20 products × 60 months = 25,200 Sales rows, which confirms the Sales grain.

### Relationships
| From (many) | To (one) | Join |
|---|---|---|
| Sales.`Store ID` | Stores.`ID` | many-to-one (column names differ) |
| Sales.`Product ID` | Products.`Product ID` | many-to-one |
| Sales.`Category ID` | Categories.`Category ID` | many-to-one |
| Sales.`StoreYear` | Survey.`Survey ID` | many-to-one, **not joined onto sales rows**: the survey is at store-year grain, so it is compared at that level only |

Channel comes from Stores.`StoreType`: `Website` (SH00) or `Brick & Mortar` (SH01–SH20).

### Merge checks (original data)
| Merge | Rows before | Rows after | Revenue sum before | Revenue sum after | Explanation |
|---|---|---|---|---|---|
| Remove exact duplicate Sales rows | 25,200 | 25,200 | $42,174,440.00 | $42,174,440.00 | No exact duplicates in the original data |
| Sales + Stores | 25,200 | 25,200 | $42,174,440.00 | $42,174,440.00 | Many-to-one, IDs unique in Stores, every Store ID matched |
| + Products | 25,200 | 25,200 | $42,174,440.00 | $42,174,440.00 | Many-to-one, every Product ID matched |
| + Categories | 25,200 | 25,200 | $42,174,440.00 | $42,174,440.00 | Many-to-one, every Category ID matched |

### How the app handles updated data
- Filter choices (years, stores, categories, channels) are read from the loaded data; nothing is typed into the code.
- A missing required column produces a plain message naming it, not a Python error.
- Blank revenue is excluded from totals and counted in the data-quality checks.
- A store, product or category ID with no match is still counted, shown as "Unknown", and flagged.
- Exact duplicate Sales rows are removed before any total (recorded in the merge checks); rows sharing a Sales ID but with different values are kept and flagged.
- Growth compares full years only (12 months of data); a partial year is named in a warning.
- File reading is cached by file contents and the joins are rebuilt on every load, so a modified upload is never shown with old numbers.

### Data-quality notes (original data)
- No blank or duplicate keys, no blank revenue, no duplicate sales rows.
- Survey: Satisfaction and Interest are blank for the 5 SH00 (website) rows, since there is no physical store to survey.
- Stores: SH00 has blank City/State/PostalCode (expected for the website); SH11's phone number is missing its opening bracket; SH07 (Flushing, NY) is labelled `Southwest`, which looks like a data-entry error.

## Metric definitions
| Metric | Definition | Does not mean |
|---|---|---|
| Revenue | Sum of the `Revenue` column on Sales rows (one product, one store, one month) | Profit: the data has no costs, so margin or profitability cannot be calculated |
| Channel | `Website` for SH00, `Brick & Mortar` for SH01–SH20, from Stores.`StoreType` | |
| Website share | Website revenue ÷ total revenue, within the current filters | |
| Revenue growth | Revenue in the last full year ÷ revenue in the first full year − 1 | Year-by-year change: years in between are shown in the charts and tool results |
| Full year | A year with data in all 12 months; partial years are excluded from growth and named in a warning | |
| Monthly share | A month's revenue ÷ that year's total (or all years in scope when no year is chosen) | |
| Satisfaction vs. revenue | Pearson correlation of survey Satisfaction (0–5) with that store's revenue in the same year, one point per store-year | Cause and effect |

## Assistant
**What we built:** a live language-model agent (**Option A**) using Google Gemini function calling, with the rules-based assistant (**Option B**) kept as an automatic fallback and as a selectable mode.

How an answer is produced:
1. Gemini receives the question and the six approved tools. Each tool's allowed values (years, stores, channels, categories) are listed from the loaded data.
2. Gemini chooses one tool and its arguments. It cannot write or run code.
3. Our dispatcher (`agent.run_tool`) rejects unknown tools or arguments, runs the tool on the loaded data, and logs the call.
4. Our code writes the **Findings** from the tool result.
5. Gemini writes the **Interpretation** and **Next steps** from the result. Any number in that text that does not appear in the tool result causes the explanation to be withheld and replaced with a standard one.
6. The answer shows its scope, who answered it, and a tool-call log (function, arguments, raw result).

| Tool | What it calculates |
|---|---|
| `get_total_revenue` | Total revenue, optionally for one year, channel, store or category |
| `get_revenue_breakdown` | Revenue and % share by channel, store, category or year |
| `get_revenue_trend` | Revenue per year and % change for each channel, store or category (full years by default) |
| `get_monthly_revenue_share` | Each month's revenue as a % of the annual total |
| `get_satisfaction_vs_revenue` | Correlation between satisfaction and revenue, across stores and within each store |
| `get_data_quality_report` | Blank, duplicate and unmatched values |

Every tool returns the same structure: `tool`, `value`, `definition`, `scope`, `rows_used`, `caveats` (and `error` when a request cannot be met).

**The assistant is not allowed to:** invent numbers; run model-written code or SQL (no `eval`/`exec`, PandasAI or pandas agents); change the loaded data; claim revenue shows profit; present correlation as causation; or make a recommendation the data cannot support. For profit, causation and "should we" questions, our code always adds the limitation and the missing information.

**Failure handling:** if Gemini is busy (503), it retries once after 2 seconds, then tries the next model. A retired model (404) or a used-up quota (429) moves straight to the next model. If all models fail, the rules-based assistant answers, and the answer says so. A bad API key is not retried.

**Scope:** the assistant answers on the full loaded dataset (dashboard filters are not applied) and says so in every answer. Name a year, store, channel or category in the question to narrow it.

**API key:** stored in Streamlit Secrets as `GEMINI_API_KEY`, never in this repository. The model list can be overridden with an optional `GEMINI_MODEL` secret (comma-separated), with no code change.

## Validation
_Sections marked [to be completed] are filled in once each check has been done in the live app._

| Metric | Power BI | App | Match? |
|---|---|---|---|
| Total revenue (2025–2029) | $42,174,440 | $42,174,440 | Yes |
| Website revenue | [to be completed] | $27,218,880 | |
| Brick & Mortar revenue | [to be completed] | $14,955,560 | |
| Revenue by year | [to be completed] | 2025 $7,181,750 · 2026 $8,016,950 · 2027 $8,824,720 · 2028 $8,969,470 · 2029 $9,181,550 | |
| Monthly revenue pattern | [to be completed] | [to be completed] | |
| Additional measure | [to be completed] | [to be completed] | |

Filter check: Year 2025 + Website = $3,160,230 in both the app and Power BI.

**Independent check:** [to be completed]

**$1,000 modified-data test:** Sales ID S0120270601 (SH01 Orlando, June 2027, Brick & Mortar, Beans) changed from $260 to $1,260 in a copy of `Sales.csv`. Uploaded with no code change and no restart: total revenue went from $42,174,440 to $42,175,440, as predicted. Filter, chart and assistant checks: [to be completed].

**Automated tests:** 61 tests (`pytest`) on small made-up tables with hand-checkable answers, covering joins, totals, filters, data-quality handling, each assistant tool, the dispatcher, and the Gemini path with a simulated model.

## Limitations
- **Revenue is not profit.** There is no cost, rent, staffing or cost-of-goods data.
- **Association is not causation.** The satisfaction analysis shows a weak positive link across stores (0.235) but a negative one within stores over time (−0.263), so it cannot show that satisfaction drives revenue.
- **The assistant ignores dashboard filters**; it answers on the full dataset unless the question names a year, store, channel or category.
- **Growth compares the first and last full years only.** Yearly values in between are in the charts.
- **The website (SH00) has no survey scores**, so it is excluded from the satisfaction analysis.
- **Two stores share a city** (SH01 and SH08, Orlando), so questions must name the store ID.
- **Rules-based mode understands keywords only**; unusual phrasing may be routed wrongly or rejected.
- **Free-tier Gemini models change and can be busy.** The fallback keeps the assistant working, but explanations may then be the standard text.
- **Data-entry oddities in Stores** (SH07 labelled Southwest; SH11's phone number) are reported, not corrected.

## Files
| File | Purpose |
|---|---|
| `streamlit_app.py` | Screen layout and interaction |
| `analytics.py` | Metrics and analytical functions (no Streamlit code) |
| `agent.py` | Tool registry, dispatcher, rules router, and Gemini assistant |
| `tests/` | 61 tests with hand-checkable answers (`test_analytics.py`, `test_epresso.py`, `test_agent.py`, `test_llm.py`) |
| `requirements.txt` | Packages the hosted app needs (streamlit, pandas, plotly, openpyxl, google-genai) |

## Run it
Hosted: deploy from this repository on share.streamlit.io with main file `streamlit_app.py`.

Locally (optional): `pip install -r requirements-dev.txt`, then `streamlit run streamlit_app.py`. Run tests with `pytest`.

## Secrets
Never commit API keys. In Streamlit Community Cloud, open the app's **Settings → Secrets** and add:
```
GEMINI_API_KEY = "your-key"
# optional: GEMINI_MODEL = "model-a, model-b"
```
For local work, copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` (git-ignored). Without a key, the app uses the rules-based assistant.
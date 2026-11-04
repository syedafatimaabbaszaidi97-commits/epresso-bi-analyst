# E-Presso BI Analyst (starter)

> Replace this README as you build. Your final version should cover every section below.

## What this application does
_One paragraph for a manager, not a programmer._

## Live app
_URL here. Note that the app may take a moment to wake up._

## How to use it
_Upload files, use filters, ask the assistant. Include the file names and sheets it expects._

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
| Sales + Stores | 25,200 | 25,200 | $42,174,440.00 | $42,174,440.00 | Many-to-one, IDs unique in Stores, every Store ID matched |
| + Products | 25,200 | 25,200 | $42,174,440.00 | $42,174,440.00 | Many-to-one, every Product ID matched |
| + Categories | 25,200 | 25,200 | $42,174,440.00 | $42,174,440.00 | Many-to-one, every Category ID matched |

### Data-quality notes (original data)
- No blank or duplicate keys, no blank revenue, no duplicate sales rows.
- Survey: Satisfaction and Interest are blank for the 5 SH00 (website) rows, since there is no physical store to survey.
- Stores: SH00 has blank City/State/PostalCode (expected for the website); SH11's phone number is missing its opening bracket; SH07 (Flushing, NY) is labelled `Southwest`, which looks like a data-entry error.

## Metric definitions
_What "revenue" means here, and anything it does not mean._

## Assistant
_State exactly what you built (live language-model agent, or rules-based assistant). List each tool, what it calculates, and what the assistant is not allowed to do._

## Validation
_Link to or summarize your Power BI comparison, independent check, and $1,000 test._

## Limitations
_Revenue is not profit. What else can this app not tell a manager?_

## Files
| File | Purpose |
|---|---|
| `streamlit_app.py` | Screen layout and interaction |
| `analytics.py` | Metrics and analytical functions (no Streamlit code) |
| `agent.py` | Tool registry, dispatcher, and routers |
| `tests/` | Tests with hand-checkable answers |
| `requirements.txt` | Packages the hosted app needs |

## Run it
Hosted: deploy from this repository on share.streamlit.io with main file `streamlit_app.py`.

Locally (optional): `pip install -r requirements-dev.txt`, then `streamlit run streamlit_app.py`. Run tests with `pytest`.

## Secrets
Never commit API keys. Use Streamlit Secrets (see `.streamlit/secrets.toml.example`).
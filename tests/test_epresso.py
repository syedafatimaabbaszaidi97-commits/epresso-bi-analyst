"""Tests for the E-Presso data model and dashboard functions.

They use a tiny made-up dataset (5 sales rows) so every expected answer can be
worked out by hand. The hand calculation is written next to each check.

    Sales ID  Date        Store  Category  Product  Revenue
    S1        2025-01-31  SH00   C1 Beans  P01      100   (website)
    S2        2025-02-28  SH01   C1 Beans  P01      200   (Orlando)
    S3        2025-01-31  SH01   C2 Syrups P02      300   (Orlando)
    S4        2026-01-31  SH00   C2 Syrups P02      400   (website)
    S5        2026-01-31  SH01   C1 Beans  P01      500   (Orlando)
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import analytics  # noqa: E402


@pytest.fixture
def tables():
    sales = pd.DataFrame({
        'Sales ID': ['S1', 'S2', 'S3', 'S4', 'S5'],
        'Date': ['2025-01-31', '2025-02-28', '2025-01-31', '2026-01-31', '2026-01-31'],
        'Store ID': ['SH00', 'SH01', 'SH01', 'SH00', 'SH01'],
        'StoreYear': ['SH00_25', 'SH01_25', 'SH01_25', 'SH00_26', 'SH01_26'],
        'Category ID': ['C1', 'C1', 'C2', 'C2', 'C1'],
        'Product ID': ['P01', 'P01', 'P02', 'P02', 'P01'],
        'Revenue': [100, 200, 300, 400, 500],
    })
    stores = pd.DataFrame({
        'ID': ['SH00', 'SH01'], 'City': [None, 'Orlando'], 'State': [None, 'FL'],
        'StoreType': ['Website', 'Brick & Mortar'], 'Market': ['5-Global', '3-Medium'],
        'Location': ['Website', 'South'], 'Manager': ['A', 'B'],
    })
    products = pd.DataFrame({'Product ID': ['P01', 'P02'], 'Product': ['French Roast', 'Vanilla']})
    categories = pd.DataFrame({'Category ID': ['C1', 'C2'], 'Category': ['Beans', 'Syrups']})
    survey = pd.DataFrame({
        'Survey ID': ['SH01_25', 'SH01_26'], 'Store ID': ['SH01', 'SH01'],
        'Year': ['Y2025', 'Y2026'], 'Satisfaction': [4.0, 4.5], 'Interest': [90, 92],
    })
    return {'Sales': sales, 'Stores': stores, 'Products': products,
            'Categories': categories, 'Survey': survey}


@pytest.fixture
def model(tables):
    m, _ = analytics.build_sales_model(tables)
    return m


# ------------------------------------------------------------- joins and total
def test_joins_keep_rows_and_revenue(tables):
    # 5 rows and $1,500 before every join, and still 5 rows and $1,500 after.
    _, checks = analytics.build_sales_model(tables)
    assert (checks['Rows before'] == 5).all() and (checks['Rows after'] == 5).all()
    assert (checks['Revenue sum after'] == 1500).all()


def test_total_revenue(model):
    # 100 + 200 + 300 + 400 + 500 = 1,500
    assert analytics.total_revenue(model) == 1500


def test_duplicate_store_in_lookup_does_not_double_count(tables):
    # If the Stores file accidentally lists SH01 twice, a naive join would turn
    # Orlando's 1,000 into 2,000. The model must still total 1,500.
    tables['Stores'] = pd.concat([tables['Stores'], tables['Stores'].iloc[[1]]])
    m, _ = analytics.build_sales_model(tables)
    assert len(m) == 5 and analytics.total_revenue(m) == 1500


# ------------------------------------------------------------- breakdowns
def test_revenue_by_channel(model):
    # Website = S1 + S4 = 100 + 400 = 500; Brick & Mortar = 200 + 300 + 500 = 1,000
    r = analytics.revenue_by(model, 'Channel').set_index('Channel')['Revenue']
    assert r['Website'] == 500 and r['Brick & Mortar'] == 1000


def test_revenue_by_year(model):
    # 2025 = 100 + 200 + 300 = 600; 2026 = 400 + 500 = 900
    r = analytics.revenue_by(model, 'Year').set_index('Year')['Revenue']
    assert r[2025] == 600 and r[2026] == 900


def test_revenue_by_category(model):
    # Beans = 100 + 200 + 500 = 800; Syrups = 300 + 400 = 700
    r = analytics.revenue_by(model, 'Category').set_index('Category')['Revenue']
    assert r['Beans'] == 800 and r['Syrups'] == 700


def test_monthly_revenue(model):
    # Jan 2025 = 100 + 300 = 400; Feb 2025 = 200; Jan 2026 = 400 + 500 = 900
    r = analytics.revenue_by(model, 'Year', 'Month').set_index(['Year', 'Month'])['Revenue']
    assert r[(2025, 1)] == 400 and r[(2025, 2)] == 200 and r[(2026, 1)] == 900


# ------------------------------------------------------------- KPIs and filters
def test_kpis(model):
    k = analytics.kpis(model)
    assert k['total'] == 1500
    assert round(k['website_share_pct'], 1) == 33.3          # 500 / 1,500
    assert k['growth'] == {'from': 2025, 'to': 2026, 'pct': 50.0}   # 900 / 600 - 1


def test_filter_year_and_channel(model):
    # 2025 website = S1 only = 100
    view = analytics.filter_model(model, {'Year': [2025], 'Channel': ['Website']})
    assert analytics.total_revenue(view) == 100


def test_empty_filter_means_all(model):
    view = analytics.filter_model(model, {'Year': None, 'Channel': None})
    assert analytics.total_revenue(view) == 1500


def test_filter_options_come_from_data(model):
    opts = analytics.filter_options(model)
    assert opts['Year'] == [2025, 2026]
    assert opts['Category'] == ['Beans', 'Syrups']
    assert opts['Store'] == ['SH00 - Website', 'SH01 - Orlando, FL']


# ------------------------------------------------------------- updated data
def test_adding_1000_raises_total_by_exactly_1000(tables):
    # The $1,000 test in miniature: S3 goes from 300 to 1,300, total 1,500 -> 2,500,
    # and only Orlando / 2025 / Syrups should change.
    tables['Sales'].loc[2, 'Revenue'] += 1000
    m, _ = analytics.build_sales_model(tables)
    assert analytics.total_revenue(m) == 2500
    by_year = analytics.revenue_by(m, 'Year').set_index('Year')['Revenue']
    assert by_year[2025] == 1600 and by_year[2026] == 900   # 2026 unchanged


def test_new_store_appears_in_filters_without_code_change(tables):
    tables['Stores'] = pd.concat([tables['Stores'], pd.DataFrame({
        'ID': ['SH02'], 'City': ['Boulder'], 'State': ['CO'], 'StoreType': ['Brick & Mortar'],
        'Market': ['2-Minor'], 'Location': ['West'], 'Manager': ['C']})])
    tables['Sales'] = pd.concat([tables['Sales'], pd.DataFrame({
        'Sales ID': ['S6'], 'Date': ['2026-03-31'], 'Store ID': ['SH02'], 'StoreYear': ['SH02_26'],
        'Category ID': ['C1'], 'Product ID': ['P01'], 'Revenue': [50]})])
    m, _ = analytics.build_sales_model(tables)
    assert 'SH02 - Boulder, CO' in analytics.filter_options(m)['Store']
    assert analytics.total_revenue(m) == 1550


# ------------------------------------------------------------- data quality
def test_missing_required_column_is_named(tables):
    assert analytics.missing_columns(tables['Sales'].drop(columns=['Revenue']), 'Sales') == ['Revenue']


def test_blank_revenue_is_excluded_and_reported(tables):
    # S5 revenue blank -> total = 1,500 - 500 = 1,000, and 1 row reported
    tables['Sales'].loc[4, 'Revenue'] = None
    m, _ = analytics.build_sales_model(tables)
    assert analytics.total_revenue(m) == 1000
    issues = analytics.sales_quality_issues(tables['Sales'], tables).set_index('Check')
    assert issues.loc['Blank or non-numeric Revenue', 'Rows affected'] == 1


def test_unknown_store_is_reported_not_dropped(tables):
    # S2 points to a store that does not exist: revenue still counted (1,500),
    # channel shown as Unknown, and the quality check flags 1 row.
    tables['Sales'].loc[1, 'Store ID'] = 'SH99'
    m, _ = analytics.build_sales_model(tables)
    assert analytics.total_revenue(m) == 1500
    assert analytics.revenue_by(m, 'Channel').set_index('Channel')['Revenue']['Unknown'] == 200
    issues = analytics.sales_quality_issues(tables['Sales'], tables).set_index('Check')
    assert issues.loc['Store ID not in Stores table', 'Rows affected'] == 1
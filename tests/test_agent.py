"""Tests for the six assistant tools, the dispatcher and the rules router.
Same tiny dataset as test_epresso.py (5 sales rows, total $1,500):

    S1 2025-01 SH00 Website   Beans   100
    S2 2025-02 SH01 Orlando   Beans   200
    S3 2025-01 SH01 Orlando   Syrups  300
    S4 2026-01 SH00 Website   Syrups  400
    S5 2026-01 SH01 Orlando   Beans   500
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import agent  # noqa: E402
import analytics  # noqa: E402
from test_epresso import tables  # noqa: E402,F401  (re-use the fixture)


@pytest.fixture
def data(tables):  # noqa: F811
    model, _ = analytics.build_sales_model(tables)
    return {'model': model, 'tables': tables}


# ------------------------------------------------------------- tools
def test_total_revenue_tool(data):
    r = analytics.get_total_revenue(data['model'], year=2025, channel='Website')
    assert r.value == 100 and r.scope['year'] == 2025 and r.rows_used == 1   # S1 only


def test_unknown_value_returns_error_not_zero(data):
    r = analytics.get_total_revenue(data['model'], year=2031)
    assert r.error and r.value is None


def test_breakdown_shares(data):
    # Website 500 / 1,500 = 33.33%; Brick & Mortar 1,000 / 1,500 = 66.67%
    v = analytics.get_revenue_breakdown(data['model'], by='channel').value
    assert v['Brick & Mortar'] == {'revenue': 1000.0, 'share_pct': 66.67}
    assert v['Website'] == {'revenue': 500.0, 'share_pct': 33.33}


def test_trend_growth(data):
    # Website 2025 = 100, 2026 = 400 -> +300%; Brick & Mortar 500 -> 500 -> 0%
    v = analytics.get_revenue_trend(data['model'], by='channel').value
    assert v[0]['group'] == 'Website' and v[0]['pct_change'] == 300.0
    assert v[1]['group'] == 'Brick & Mortar' and v[1]['pct_change'] == 0.0


def test_trend_says_when_no_group_declined(data):
    r = analytics.get_revenue_trend(data['model'], by='channel')
    assert 'No group declined over this period.' in r.caveats


def test_monthly_share(data):
    # 2025: Jan = 100 + 300 = 400 of 600 = 66.67%; Feb = 200 of 600 = 33.33%
    v = analytics.get_monthly_revenue_share(data['model'], year=2025).value
    assert v['Jan']['share_pct'] == 66.67 and v['Feb']['share_pct'] == 33.33


def test_store_by_city(data):
    assert analytics.get_total_revenue(data['model'], store='Orlando').value == 1000  # 200+300+500


def test_data_quality_tool(data):
    v = analytics.get_data_quality_report(data['model'], data['tables']).value
    assert v['Duplicate Sales ID'] == 0


def test_tools_do_not_change_the_data(data):
    before = data['model'].copy()
    for name in agent.TOOLS:
        agent.run_tool(name, {}, data, [])
    pd.testing.assert_frame_equal(before, data['model'])


# ------------------------------------------------------------- dispatcher
def test_dispatcher_logs_every_call(data):
    log = []
    agent.run_tool('get_total_revenue', {'year': 2026}, data, log)
    assert log[0]['tool'] == 'get_total_revenue'
    assert log[0]['arguments'] == {'year': 2026}
    assert log[0]['result']['value'] == 900.0          # 400 + 500


def test_dispatcher_rejects_unknown_tool_and_arguments(data):
    assert agent.run_tool('run_python', {}, data, []).error
    assert agent.run_tool('get_total_revenue', {'sql': 'DROP TABLE'}, data, []).error


# ------------------------------------------------------------- router and answers
def test_numerical_question(data):
    out = agent.answer_question('How much revenue did the website make in 2026?', data)
    assert out['tool_log'][0]['arguments'] == {'channel': 'Website', 'year': 2026}
    assert '$400.00' in out['findings'] and 'year: 2026' in out['scope']


def test_comparison_question(data):
    out = agent.answer_question('Which channel grew the most?', data)
    assert out['tool_log'][0]['tool'] == 'get_revenue_trend' and 'Website' in out['findings']


def test_profit_question_states_limitation(data):
    out = agent.answer_question('Which store is the most profitable?', data)
    assert 'cannot measure profit' in out['interpretation']


def test_causation_question(data):
    out = agent.answer_question('Does higher satisfaction cause higher revenue?', data)
    assert out['tool_log'][0]['tool'] == 'get_satisfaction_vs_revenue'
    assert 'not cause' in out['interpretation']


def test_answer_changes_when_data_changes(tables):  # noqa: F811
    q = 'What is total revenue?'
    m1, _ = analytics.build_sales_model(tables)
    before = agent.answer_question(q, {'model': m1, 'tables': tables})
    tables['Sales'].loc[2, 'Revenue'] += 1000
    m2, _ = analytics.build_sales_model(tables)
    after = agent.answer_question(q, {'model': m2, 'tables': tables})
    assert '$1,500.00' in before['findings'] and '$2,500.00' in after['findings']


def test_unsupported_question(data):
    out = agent.answer_question('What is the capital of France?', data)
    assert 'can only answer' in out['findings'] and out['tool_log'] == []
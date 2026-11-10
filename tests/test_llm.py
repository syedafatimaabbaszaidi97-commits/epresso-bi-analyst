"""Tests for the Gemini (Option A) assistant, using a FAKE model so they run
without an API key or internet. They check our side of the contract: we run the
tool, findings come from the tool, invented numbers are blocked, and failures
fall back to the rules-based assistant."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import agent  # noqa: E402
import analytics  # noqa: E402
from test_epresso import tables  # noqa: E402,F401


class QuotaError(Exception):
    pass


@pytest.fixture
def data(tables):  # noqa: F811
    model, _ = analytics.build_sales_model(tables)
    return {'model': model, 'tables': tables}


def fake_gemini(monkeypatch, choose, explain=None):
    """Replace the two Gemini calls with fakes."""
    monkeypatch.setattr(agent, '_gemini_choose_tool', lambda client, name, q, schemas: choose(name))
    monkeypatch.setattr(agent, '_gemini_explain',
                        lambda client, name, q, result: (explain or (lambda r: {
                            'interpretation': 'Website revenue was a smaller share.',
                            'next_steps': 'Compare with the prior year.'}))(result))


def test_schema_values_come_from_loaded_data(data):
    schemas = {s['name']: s for s in agent.tool_schemas(data['model'])}
    props = schemas['get_total_revenue']['parameters']['properties']
    assert props['year']['enum'] == ['2025', '2026']
    assert props['channel']['enum'] == ['Brick & Mortar', 'Website']
    assert set(schemas) == set(agent.TOOLS)


def test_model_picks_tool_and_our_code_runs_it(monkeypatch, data):
    # Model asks for 2026 website revenue (years arrive as strings). S4 = 400.
    fake_gemini(monkeypatch, lambda name: ('get_total_revenue', {'year': '2026', 'channel': 'Website'}))
    out = agent.answer_question_llm('Website revenue in 2026?', data, client=None)
    assert '$400.00' in out['findings']
    assert out['answered_by'].startswith('Gemini')
    assert out['tool_log'][1]['arguments'] == {'year': 2026, 'channel': 'Website'}


def test_invented_number_in_explanation_is_withheld(monkeypatch, data):
    fake_gemini(monkeypatch, lambda name: ('get_total_revenue', {}),
                lambda r: {'interpretation': 'Revenue was $9,999,999 last year.', 'next_steps': ''})
    out = agent.answer_question_llm('Total revenue?', data, client=None)
    assert '9,999,999' not in out['interpretation']
    assert 'withheld' in out['answered_by']
    assert out['tool_log'][-1]['numbers_not_in_tool_result'] == [9999999.0]


def test_numbers_from_the_result_are_allowed(data):
    result = analytics.get_total_revenue(data['model']).to_dict()      # 1500.0
    assert agent.unsupported_numbers('Total was $1,500 across 5 rows in 2025.', result) == []
    assert agent.unsupported_numbers('Total was $2,700.', result) == [2700.0]


def test_missing_model_name_tries_the_next_one(monkeypatch, data):
    def choose(name):
        if name == 'old-model':
            raise RuntimeError('404 NOT_FOUND. models/old-model is not found')
        return ('get_total_revenue', {})
    fake_gemini(monkeypatch, choose)
    out = agent.answer_question_llm('Total revenue?', data, client=None,
                                    model_names=['old-model', 'good-model'])
    assert out['answered_by'].startswith('Gemini (good-model)')


def test_quota_error_falls_back_to_rules(monkeypatch, data):
    def choose(name):
        raise QuotaError('429 RESOURCE_EXHAUSTED')
    fake_gemini(monkeypatch, choose)
    out = agent.answer_question_llm('What is total revenue?', data, client=None)
    assert out['answered_by'].startswith('Rules-based assistant (Gemini unavailable')
    assert '$1,500.00' in out['findings']


def test_no_tool_chosen_means_unsupported(monkeypatch, data):
    fake_gemini(monkeypatch, lambda name: None)
    out = agent.answer_question_llm('Tell me a joke', data, client=None)
    assert 'can only answer' in out['findings']


def test_profit_limitation_is_always_stated(monkeypatch, data):
    # Even if the model forgets to say it, our code adds the profit limitation.
    fake_gemini(monkeypatch, lambda name: ('get_revenue_breakdown', {'by': 'store'}),
                lambda r: {'interpretation': 'SH01 leads.', 'next_steps': 'Review store results.'})
    out = agent.answer_question_llm('Which store is the most profitable?', data, client=None)
    assert 'cannot measure profit' in out['interpretation']
    assert 'operating costs' in out['next_steps']


def test_model_cannot_pass_unknown_arguments(monkeypatch, data):
    fake_gemini(monkeypatch, lambda name: ('get_total_revenue', {'sql': 'DROP TABLE sales'}))
    out = agent.answer_question_llm('Total?', data, client=None)
    assert 'could not be completed' in out['findings']
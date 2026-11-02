"""The whole Streamlit script, run headless with AppTest against fake marts and real model artifacts."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import dashboard.queries as queries
from dashboard.config import get_settings
from tests.conftest import fake_marts

APP = str(Path(__file__).resolve().parents[1] / "src" / "dashboard" / "app.py")
DBT_SOURCES = Path(__file__).resolve().parents[2] / "dbt" / "credit_risk" / "models" / "staging"
PERIOD_HOURS = {"hour": 1, "day": 24}


@pytest.fixture
def app(monkeypatch, artifacts_dir):
    marts = fake_marts()
    monkeypatch.setattr(queries, "fetch", lambda name, url: marts[name].copy())
    monkeypatch.setenv("ARTIFACTS_DIR", str(artifacts_dir))
    get_settings.cache_clear()
    st.cache_data.clear()
    st.cache_resource.clear()
    yield AppTest.from_file(APP, default_timeout=120)
    get_settings.cache_clear()


def metric(at, label):
    return next(m.value for m in at.metric if m.label == label)


def test_all_three_tabs_render_without_exceptions(app):
    app.run()
    assert not app.exception
    assert [t.label for t in app.tabs] == ["Engineering", "Portfolio", "Risk simulation"]
    assert metric(app, "Dates loaded") == "10"
    # 36 defaults / 315 labeled: the 2025-08 cohort is left out because payroll loans there are immature
    assert metric(app, "Default rate (mature cohorts)") == "11.4%"
    assert any("1 above the 5.5% warning budget, 0 above the 9% error budget" in c.value for c in app.caption)


def test_simulation_responds_to_the_bureau_score(app):
    app.run()
    baseline = float(metric(app, "Probability of default").rstrip("%"))
    app.number_input(key="bureau_score_at_origination").set_value(250).run()
    risky = float(metric(app, "Probability of default").rstrip("%"))
    assert not app.exception
    assert risky > baseline
    assert metric(app, "Risk band") in {"A", "B", "C", "D", "E"}


def test_without_a_trained_model_the_tab_explains_instead_of_failing(app, monkeypatch, tmp_path):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    get_settings.cache_clear()
    app.run()
    assert not app.exception
    assert any("make train" in i.value for i in app.info)


def test_artifacts_of_an_older_model_are_reported_instead_of_failing(app, monkeypatch, outdated_artifacts_dir):
    monkeypatch.setenv("ARTIFACTS_DIR", str(outdated_artifacts_dir))
    get_settings.cache_clear()
    app.run()
    assert not app.exception
    assert any("Model artifacts are out of date: run `make train`" in w.value for w in app.warning)


def test_a_warehouse_outage_is_reported_not_raised(app, monkeypatch):
    def down(name, url):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(queries, "fetch", down)
    app.run()
    assert not app.exception
    assert any("Could not read" in e.value for e in app.error)


def dbt_freshness_hours(level: str) -> int:
    """The single warn_after / error_after of every dbt source, in hours."""
    pattern = rf"{level}_after:\s*\{{count:\s*(\d+),\s*period:\s*(\w+)\}}"
    found = {
        int(count) * PERIOD_HOURS[period]
        for path in DBT_SOURCES.rglob("_*sources.yml")
        for count, period in re.findall(pattern, path.read_text(encoding="utf-8"))
    }
    assert len(found) == 1, found
    return found.pop()


def freshness_status(app, monkeypatch, hours_ago: float) -> str:
    marts = fake_marts()
    marts["pipeline_freshness"].loc[0, "last_load_at"] = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        hours=hours_ago
    )
    monkeypatch.setattr(queries, "fetch", lambda name, url: marts[name].copy())
    st.cache_data.clear()
    app.run()
    return next(m.delta for m in app.metric if m.label == "Last load (UTC)").rsplit(": ", 1)[1]


def test_freshness_status_follows_the_dbt_source_freshness(app, monkeypatch):
    warn, error = dbt_freshness_hours("warn"), dbt_freshness_hours("error")
    assert freshness_status(app, monkeypatch, warn - 1) == "OK"
    assert freshness_status(app, monkeypatch, warn + 1) == "WARN"
    assert freshness_status(app, monkeypatch, error + 1) == "ERROR"


def test_odds_ratios_are_read_against_the_reference_category(app):
    app.run()
    headings = " ".join(s.value for s in app.subheader)
    assert "vs. the reference category" in headings
    assert "vs. the rest" not in headings

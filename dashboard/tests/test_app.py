"""The whole Streamlit script, run headless with AppTest against fake marts and real model artifacts."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import dashboard.queries as queries
from dashboard.config import get_settings
from tests.conftest import fake_marts

APP = str(Path(__file__).resolve().parents[1] / "src" / "dashboard" / "app.py")


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
    assert metric(app, "Default rate (labeled)") == "11.4%"  # 36 defaults / 315 labeled
    assert any("4 entity-dates above" in c.value for c in app.caption)


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


def test_a_warehouse_outage_is_reported_not_raised(app, monkeypatch):
    def down(name, url):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(queries, "fetch", down)
    app.run()
    assert not app.exception
    assert any("Could not read" in e.value for e in app.error)

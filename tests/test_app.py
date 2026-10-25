"""Smoke test the Streamlit app headlessly in demo mode."""
from pathlib import Path

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest
APP = str(Path(__file__).resolve().parent.parent / "app.py")


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("CRYPTODASH_DATA", str(tmp_path))
    return tmp_path


def run_demo() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    at.radio[0].set_value("Demo market (offline)")
    at.run()
    return at


def test_app_renders_demo_market():
    at = run_demo()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["🌍 Market", "🎯 Trade plans", "📊 Chart", "💼 Demo account", "🧠 Skills"]
    assert len(at.dataframe) >= 1


def test_app_auto_trades_and_fast_forwards(isolated_data):
    at = run_demo()
    at.toggle[0].set_value(True)                 # auto paper-trade
    at.run()
    for _ in range(3):
        next(b for b in at.button if b.label == "+24h").click()
        at.run()
    assert not at.exception
    assert (isolated_data / "paper_account.json").exists()
    assert (isolated_data / "demo_clock.json").exists()

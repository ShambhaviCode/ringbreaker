import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from graph.load import load_tenant  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def fresh_graphs():
    """Reload both banks once per test session so tests start from the same synthetic data."""
    if not (ROOT / "data" / "out" / "demobank.json").exists():
        from data.generate import main as gen

        sys.argv = ["generate"]
        gen()
    for t in config.TENANTS:
        load_tenant(t, verbose=False)
    yield


@pytest.fixture(scope="session")
def truth():
    return json.loads((ROOT / "data" / "out" / "demobank.json").read_text())["ground_truth"]

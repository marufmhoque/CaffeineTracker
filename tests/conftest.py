import copy
import sys
from pathlib import Path

import pytest
from sqlmodel import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import config  # noqa: E402
from backend.app.costing import DEFAULT_RULES  # noqa: E402
from backend.app.db import get_engine, init_db, set_engine  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def rules():
    return copy.deepcopy(DEFAULT_RULES)


@pytest.fixture()
def tmp_env(tmp_path, monkeypatch):
    """Isolated data dir + database; global engine swapped for the test."""
    monkeypatch.setattr(config, "FREIGHT_RULES_FILE", tmp_path / "freight_rules.json")
    monkeypatch.setattr(config, "SPOT_SHEETS_DIR", tmp_path / "spot_sheets")
    monkeypatch.setattr(config, "ICO_PRICES_FILE", tmp_path / "ico_prices.csv")
    monkeypatch.setattr(config, "PORTALS_FILE", tmp_path / "portals.json")
    monkeypatch.setattr(config, "ENABLE_SCHEDULER", False)
    engine = get_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    init_db(engine)
    set_engine(engine)
    yield engine
    engine.dispose()
    set_engine(None)


@pytest.fixture()
def session(tmp_env):
    with Session(tmp_env) as s:
        yield s

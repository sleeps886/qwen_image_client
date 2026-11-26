"""Never let application lifespan recovery inspect a user's live ledger."""
import pytest
from backend.app.api.routers import live_tracking


@pytest.fixture(autouse=True)
def isolated_live_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(live_tracking, 'LIVE_DB', tmp_path/'live-ledger.sqlite3')

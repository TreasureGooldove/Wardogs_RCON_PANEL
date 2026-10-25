"""Keep background public-catalog polling offline in the test suite."""
import httpx
import pytest
from app.game_rules.catalog import ItemCatalogService


@pytest.fixture(autouse=True)
def offline_item_catalog(monkeypatch):
    original=ItemCatalogService.__init__
    def initialize(self, database, *, transport=None):
        if transport is None:
            transport=httpx.MockTransport(lambda request:httpx.Response(503))
        original(self,database,transport=transport)
    monkeypatch.setattr(ItemCatalogService,'__init__',initialize)

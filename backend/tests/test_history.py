"""History is durable and boundaries are explicitly inferred from fresh snapshots."""

from datetime import UTC, datetime, timedelta

from app.history.store import HistoryStore
from app.storage.db import Database


def test_history_persists_players_and_marks_observed_match_changes(tmp_path):
    database = Database(tmp_path / "panel.sqlite3")
    database.initialize()
    store = HistoryStore(database)
    store.initialize()
    t0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    first = {"map": "Map A", "experiences": ["Main"], "lighting": "Day",
             "factionScores": [{"name": "Lonestar", "score": 20}],
             "rotation": {"nowIndex": 0}, "scoreTick": None, "matchSeconds": None}
    roster = {"players": [{"steamId": "76561190000000001", "name": "Test Player",
                           "faction": "BLU", "kills": 2, "deaths": 1, "cash": 5, "pingMs": 42}]}
    first_id = store.record("http://example.test", first, roster, t0)
    assert store.record("http://example.test", first, roster, t0 + timedelta(seconds=5)) == first_id
    assert HistoryStore(database).player("http://example.test", "76561190000000001")["matches"][0]["sample_count"] == 2
    second = {**first, "map": "Map B"}
    second_id = store.record("http://example.test", second, roster, t0 + timedelta(seconds=10))
    assert second_id != first_id
    assert store.match("http://example.test", first_id)["end_reason"] == "map_changed"
    assert store.match("http://example.test", second_id)["players"][0]["kills"] == 2
    assert store.players("http://example.test", "Test", 30, 0)["items"][0]["match_count"] == 2
    assert store.matches("http://other.test", 30, 0)["total"] == 0
    third_id = store.record("http://example.test", second, roster, t0 + timedelta(minutes=4))
    assert third_id != second_id
    assert store.match("http://example.test", second_id)["end_reason"] == "observation_gap"

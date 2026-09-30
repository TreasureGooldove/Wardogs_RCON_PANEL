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


def test_totals_deduplicate_samples_handle_round_reset_and_persist(tmp_path):
    database = Database(tmp_path / 'panel.sqlite3')
    database.initialize()
    store = HistoryStore(database)
    store.initialize()
    t0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
    status = {'map': 'A', 'experiences': ['Main']}
    player = {'steamId': '76561190000000001', 'name': 'First', 'kills': 10, 'deaths': 2, 'cash': 100}
    def sample(seconds, **values):
        store.record('https://example.test', status, {'players': [{**player, **values}]}, t0 + timedelta(seconds=seconds))
    sample(0)
    sample(5)
    sample(10, kills=12, deaths=3, cash=80, name='Renamed')
    sample(180, kills=12, deaths=3, cash=90, name='Renamed')  # Observation gap, same counters.
    status['map'] = 'B'
    sample(185, kills=1, deaths=0, cash=50, name='Renamed')
    sample(190, kills=3, deaths=1, cash=200, name='Renamed')
    sample(195, kills=None, deaths=None, cash=None, name='Renamed')
    sample(200, kills=3, deaths=1, cash=150, name='Renamed')
    row = store.players('https://example.test', '', 30, 0)['items'][0]
    assert (row['total_kills'], row['total_deaths'], row['latest_cash'], row['peak_cash']) == (15, 4, 150, 200)
    assert row['name'] == 'Renamed'
    store.initialize()  # Restart does not replay the migration or recount.
    assert store.player('https://example.test', player['steamId'])['totals']['total_kills'] == 15
    assert store.players('https://other.test', '', 30, 0)['items'] == []


def test_existing_history_backfills_once(tmp_path):
    database = Database(tmp_path / 'panel.sqlite3')
    database.initialize()
    store = HistoryStore(database)
    store.initialize()
    store.record('https://example.test', {'map': 'A'}, {'players': [{'steamId': '76561190000000001', 'name': 'Test', 'kills': 3, 'deaths': 2, 'cash': 50}]})
    with database._connect() as db:
        db.execute('DROP TABLE player_totals')
    store.initialize()
    assert store.player('https://example.test', '76561190000000001')['totals'] == {'total_kills': 3, 'total_deaths': 2, 'latest_cash': 50, 'peak_cash': 50}

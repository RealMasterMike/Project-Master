from pathlib import Path

from project_master.memory.store import SQLiteStore


def test_memory_round_trip(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.remember("project", "name", "Project Master", source="test", confidence=0.9)
    rows = store.recall(query="Project Master")
    assert rows[0]["value"] == "Project Master"
    assert rows[0]["source"] == "test"


def test_claim_and_evidence(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    claim_id = store.record_claim("A test claim")
    store.add_evidence(claim_id, "supports", "A test observation", reliability=0.7)
    claims = store.list_claims()
    assert claims[0]["id"] == claim_id
    assert claims[0]["evidence"][0]["stance"] == "supports"


def test_conversation_history_order(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    session = store.create_session()
    store.add_message(session, "user", "one")
    store.add_message(session, "assistant", "two")
    assert store.recent_messages(session) == [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "two"},
    ]

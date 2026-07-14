from pathlib import Path

from project_master.memory.store import SQLiteStore
from project_master.personality.profile import StyleProfiler


def test_profile_observation_persists(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    profiler = StyleProfiler(store)
    before = profiler.profile.profanity_tolerance
    profiler.observe(
        "This is fucking direct, but it is also a technical model architecture question."
    )
    assert profiler.profile.profanity_tolerance > before
    reloaded = StyleProfiler(store)
    assert reloaded.profile.observations == 1

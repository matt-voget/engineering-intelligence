import threading
import time
from types import SimpleNamespace

from engineering_intelligence import individual_cache


def test_materialize_individuals_is_bounded_and_complete(tmp_path, monkeypatch):
    active = 0
    max_active = 0
    lock = threading.Lock()
    cached = []

    class FakeQuery:
        def __init__(self, *_args, **_kwargs):
            pass

        def get(self, snapshot_id, person_id):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return SimpleNamespace(snapshot_id=snapshot_id, person_id=person_id)

    monkeypatch.setattr(individual_cache, "IndividualQuery", FakeQuery)
    monkeypatch.setattr(
        individual_cache,
        "cache_individual",
        lambda _root, individual: cached.append(individual.person_id),
    )
    monkeypatch.setattr(individual_cache, "INDIVIDUAL_CACHE_WORKERS", 2)
    teams_config = SimpleNamespace(
        teams=[
            SimpleNamespace(
                members=[
                    SimpleNamespace(id=str(index), active=True) for index in range(5)
                ]
            )
        ]
    )

    count = individual_cache.materialize_individuals(
        tmp_path, "snapshot", object(), teams_config
    )

    assert count == 5
    assert sorted(cached) == [str(index) for index in range(5)]
    assert max_active == 2

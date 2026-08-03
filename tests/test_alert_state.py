from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from openportfolio.domain import Alert, Severity
from openportfolio.persistence import AlertState, AlertStateError, JsonAlertStateStore


NOW = datetime(2026, 1, 2, 16, 0, tzinfo=timezone.utc)


def _alert(identifier: str = "alert-test", severity: Severity = Severity.REVIEW) -> Alert:
    return Alert(
        id=identifier,
        event_id=f"event-{identifier}",
        severity=severity,
        title="OpenPortfolio test",
        body="Fictitious test alert",
        created_at=NOW,
    )


def test_first_load_without_state_file_is_empty(tmp_path: Path) -> None:
    store = JsonAlertStateStore(tmp_path / "missing" / "alert_state.json")

    state = store.load()

    assert not state.delivered_alerts
    assert not store.path.exists()


def test_state_round_trip_creates_parent_directory(tmp_path: Path) -> None:
    store = JsonAlertStateStore(tmp_path / "nested" / "alert_state.json")
    state = AlertState.empty().with_delivered(_alert())

    store.save(state)

    loaded = store.load()
    assert loaded == state
    assert store.path.read_text(encoding="utf-8").startswith("{\n")
    assert '"version": 1' in store.path.read_text(encoding="utf-8")


def test_corrupt_json_is_reported_and_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "alert_state.json"
    original = "{not-json\n"
    path.write_text(original, encoding="utf-8")
    store = JsonAlertStateStore(path)

    with pytest.raises(AlertStateError, match="no se pudo leer.*alert_state.json"):
        store.load()

    assert path.read_text(encoding="utf-8") == original


def test_failed_atomic_replace_preserves_previous_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "alert_state.json"
    store = JsonAlertStateStore(path)
    store.save(AlertState.empty().with_delivered(_alert("alert-old")))
    original = path.read_bytes()

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("openportfolio.persistence.alert_state.os.replace", fail_replace)

    with pytest.raises(AlertStateError, match="no se pudo guardar"):
        store.save(AlertState.empty().with_delivered(_alert("alert-new")))

    assert path.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob(".*.tmp")) == []

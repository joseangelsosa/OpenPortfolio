from pathlib import Path

import yaml


WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "portfolio-review.yml"


def _workflow() -> dict[str, object]:
    with WORKFLOW.open(encoding="utf-8") as stream:
        return yaml.load(stream, Loader=yaml.BaseLoader)


def test_workflow_has_madrid_weekday_schedule() -> None:
    workflow = _workflow()
    schedule = workflow["on"]["schedule"]  # type: ignore[index]

    assert schedule == [
        {
            "cron": "5 8,12,16,20 * * 1-5",
            "timezone": "Europe/Madrid",
        }
    ]


def test_schedule_and_manual_operation_share_real_review_path() -> None:
    workflow = _workflow()
    dispatch_options = workflow["on"]["workflow_dispatch"]["inputs"]["operation"][  # type: ignore[index]
        "options"
    ]
    steps = workflow["jobs"]["review"]["steps"]  # type: ignore[index]
    real_step = next(step for step in steps if step.get("id") == "review_and_notify")

    assert dispatch_options == [
        "fake",
        "dry-run",
        "check-real-quotes",
        "send-test-notification",
        "review-and-notify",
    ]
    assert "github.event_name == 'schedule'" in real_step["if"]
    assert "inputs.operation == 'review-and-notify'" in real_step["if"]
    assert "--provider yfinance" in real_step["run"]
    assert "--notifier ntfy" in real_step["run"]
    assert "--operational-notification" in real_step["run"]


def test_real_state_is_restored_saved_and_reviews_are_not_cancelled() -> None:
    workflow = _workflow()
    assert workflow["concurrency"] == {  # type: ignore[index]
        "group": "portfolio-review",
        "cancel-in-progress": "false",
    }
    steps = workflow["jobs"]["review"]["steps"]  # type: ignore[index]
    restore = next(step for step in steps if step["name"] == "Restore alert delivery state")
    save = next(step for step in steps if step["name"] == "Save updated alert delivery state")

    assert "github.event_name == 'schedule'" in restore["if"]
    assert "github.event_name == 'schedule'" in save["if"]

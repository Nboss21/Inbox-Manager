from app.tasks.rules import should_create_task
from app.tasks.models import Site24x7Alert


def _alert(status: str) -> Site24x7Alert:
	return Site24x7Alert(
		monitor_name="API Health Check",
		status=status,
		monitor_url="https://api.example.com/health",
		incident_reason="HTTP 500 returned by the target endpoint",
		dashboard_link="https://www.site24x7.com/app/monitor-details/123456",
	)


def test_should_create_task_for_down() -> None:
	assert should_create_task(_alert("DOWN")) is True


def test_should_not_create_task_for_up() -> None:
	assert should_create_task(_alert("UP")) is False


def test_should_create_task_for_trouble() -> None:
	assert should_create_task(_alert("TROUBLE")) is True


def test_should_not_create_task_for_unknown_status() -> None:
	assert should_create_task(_alert("MAINTENANCE")) is False
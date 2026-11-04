"""The deployed application owns database startup and shutdown."""
import sqlite3
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from schedule_api.__main__ import main
from schedule_core.core.settings import Settings


def test_fresh_database_without_model_credentials(tmp_path):
    database = tmp_path / "profile" / "schedule.db"
    app = main(Settings(
        _env_file=None, database_url=f"sqlite+aiosqlite:///{database}",
        api_keys=["test-key"], llm_api_key=None,
    ))
    service = app.state.db_service
    with patch.object(service, "close", new=AsyncMock(wraps=service.close)) as close:
        with TestClient(app) as client:
            response = client.post("/api/rpc/", headers={"Authorization": "Bearer test-key"}, json={
                "jsonrpc": "2.0", "id": "startup", "method": "tasks/send", "params": {
                    "id": "startup-task", "message": {"role": "user", "parts": [{
                        "type": "data", "data": {"intent": "query_events", "payload": {
                            "start_time": "2030-01-07T00:00:00Z", "end_time": "2030-01-08T00:00:00Z",
                        }},
                    }]},
                },
            })
            assert response.status_code == 200
            result = response.json()
            assert result.get("error") is None, result
            assert result["result"]["status"]["state"] == "completed"
        close.assert_awaited_once()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0] > 0

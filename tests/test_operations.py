from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient

from askdata.api.app import app


@app.get("/_test/unhandled-error")
async def _raise_unhandled_error():
    raise RuntimeError("sensitive internal detail")


def test_health_and_metrics_endpoints_are_machine_readable():
    client = TestClient(app, backend_options={"use_uvloop": True})
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.headers["x-request-id"]

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "askdata_database_count" in metrics.text
    assert metrics.headers["content-type"].startswith("text/plain")


def test_unhandled_errors_return_safe_code_and_request_id():
    client = TestClient(
        app, backend_options={"use_uvloop": True}, raise_server_exceptions=False
    )

    response = client.get(
        "/_test/unhandled-error", headers={"X-Request-ID": "request-test-123"}
    )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "request-test-123"
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "服务暂时无法处理请求，请稍后重试",
            "request_id": "request-test-123",
        }
    }
    assert "sensitive internal detail" not in response.text

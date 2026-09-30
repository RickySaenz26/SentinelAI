import json
from pathlib import Path

from app.api.v1.dependencies import SESSION_COOKIE
from app.core.config import get_settings
from app.main import app
from tests.assets_contract import assets_contract


def test_asset_openapi_matches_reviewed_contract():
    expected = json.loads((Path(__file__).parent / "contracts" / "assets.openapi.json").read_text())
    assert assets_contract(app.openapi(), get_settings().csrf_header_name) == expected


def test_asset_contract_declares_runtime_cookie_and_csrf_requirements():
    snapshot = json.loads((Path(__file__).parent / "contracts" / "assets.openapi.json").read_text())
    for document, csrf_name in (
        (app.openapi(), get_settings().csrf_header_name),
        (snapshot, "X-CSRF-Token"),
    ):
        cookie = document["components"]["securitySchemes"]["SessionCookie"]
        assert cookie == {"type": "apiKey", "in": "cookie", "name": SESSION_COOKIE}
        for path, operations in document["paths"].items():
            if path != "/api/v1/assets" and not path.startswith("/api/v1/assets/"):
                continue
            for method, operation in operations.items():
                assert operation["security"] == [{"SessionCookie": []}]
                csrf = [
                    parameter
                    for parameter in operation["parameters"]
                    if parameter["in"] == "header" and parameter["name"] == csrf_name
                ]
                assert len(csrf) == 1
                if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                    assert csrf[0]["required"] is True
                    assert csrf[0]["schema"]["type"] == "string"
                else:
                    assert csrf[0]["required"] is False


def test_asset_contract_auth_requirements_are_enforced(client, login):
    login()
    assert client.get("/api/v1/assets").status_code == 200
    asset_path = "/api/v1/assets/00000000-0000-0000-0000-000000000001"
    mutations = (
        (
            "POST",
            "/api/v1/assets",
            {"type": "ipv4", "target": "192.0.2.10", "display_name": "Lab", "criticality": "low"},
            {"Idempotency-Key": "contract-create"},
        ),
        (
            "PATCH",
            asset_path,
            {"display_name": "Updated"},
            {"If-Match": "1"},
        ),
        (
            "DELETE",
            asset_path,
            {"reason": "Contract check"},
            {"If-Match": "1", "Idempotency-Key": "contract-archive"},
        ),
    )
    for method, path, body, headers in mutations:
        response = client.request(
            method, path, json=body, headers={"Origin": "https://testserver", **headers}
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CSRF_VALIDATION_FAILED"
    client.cookies.clear()
    assert client.get("/api/v1/assets").status_code == 401

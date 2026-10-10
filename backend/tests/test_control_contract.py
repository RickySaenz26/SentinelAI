"""Frozen eight-operation 4B contract, separate from historical assets/evidence."""

import json
from pathlib import Path

from app.core.config import get_settings
from app.main import app
from tests.assets_contract import assets_contract


def control_contract():
    document = app.openapi()
    selected = {path for path in document["paths"] if "/control-" in path}
    return assets_contract(
        document,
        get_settings().csrf_header_name,
        selected_paths=selected,
        title="SentinelAI technical control — stage 4B",
    )


def test_frozen_contract_and_security_semantics():
    actual = control_contract()
    expected = json.loads((Path(__file__).parent / "contracts/control.openapi.json").read_text())
    assert actual == expected
    assert sum(len(operations) for operations in actual["paths"].values()) == 8
    for operations in actual["paths"].values():
        for method, operation in operations.items():
            assert operation["security"] == [{"SessionCookie": []}]
            headers = {p["name"]: p for p in operation["parameters"] if p["in"] == "header"}
            assert headers["X-CSRF-Token"]["required"] == (method == "post")
            for code, response in operation["responses"].items():
                assert response["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
                assert ("Idempotency-Replayed" in response["headers"]) == (
                    method == "post" and code == "201"
                )
            if method == "post":
                assert headers["If-Match"]["required"] and headers["Idempotency-Key"]["required"]
                assert not headers["Origin"]["required"]
                body = operation["requestBody"]["content"]["application/json"]
                assert body["x-max-bytes"] == 16384
                assert body["schema"]["additionalProperties"] is False
    summary = actual["components"]["schemas"]["ControlSummary"]
    assert set(summary["properties"]) == {"asset_id", "review_count", "ownership_status"}

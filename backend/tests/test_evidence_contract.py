"""Frozen evidence subset; preserve the historical five-operation assets snapshot."""

import json
from pathlib import Path

from app.core.config import get_settings
from app.main import app
from tests.assets_contract import assets_contract


def evidence_contract():
    return assets_contract(
        app.openapi(),
        get_settings().csrf_header_name,
        prefix="/api/v1/evidence/",
        title="SentinelAI laboratory evidence — increment 2 stage 3",
    )


def test_evidence_contract_and_requirements():
    actual = evidence_contract()
    expected = json.loads((Path(__file__).parent / "contracts/evidence.openapi.json").read_text())
    assert actual == expected
    assert sum(len(operations) for operations in actual["paths"].values()) == 5
    for operations in actual["paths"].values():
        for method, operation in operations.items():
            assert operation["security"] == [{"SessionCookie": []}]
            headers = {p["name"]: p for p in operation["parameters"] if p["in"] == "header"}
            assert headers["X-CSRF-Token"]["required"] == (method == "post")
            for status, response in operation["responses"].items():
                declared = response["headers"]
                assert declared["Cache-Control"]["schema"] == {
                    "type": "string",
                    "const": "no-store",
                }
                assert ("Idempotency-Replayed" in declared) == (
                    method == "post" and status == "201"
                )
            assert ("Origin" in headers) == (method == "post")
            if method == "post":
                assert headers["Origin"]["required"] is False
                assert "403" in headers["Origin"]["description"]
                assert operation["responses"]["201"]["headers"]["Idempotency-Replayed"][
                    "schema"
                ] == {"type": "string", "enum": ["true", "false"]}
                assert headers["If-Match"]["required"]
                assert headers["Idempotency-Key"]["required"]
                content = operation["requestBody"]["content"]["application/json"]
                assert content["x-max-bytes"] == 16384
                assert content["schema"]["additionalProperties"] is False

    def references(value):
        if isinstance(value, dict):
            if "$ref" in value:
                name = value["$ref"].removeprefix("#/components/schemas/")
                assert name in actual["components"]["schemas"]
            for item in value.values():
                references(item)
        elif isinstance(value, list):
            for item in value:
                references(item)

    references(actual)

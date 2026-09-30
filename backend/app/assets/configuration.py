"""Operator-owned configuration, separate from client request bodies."""

import json
import os

from pydantic import ValidationError

from app.assets.policy import LabPolicy


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate policy key")
        result[key] = value
    return result


def get_lab_policy() -> LabPolicy | None:
    # No default targets, file downloads, DNS lookups or network operations.
    raw = os.environ.get("LAB_ASSET_POLICY_JSON", "")
    if len(raw.encode("utf-8")) > 350000:
        return None
    try:
        json.loads(raw, object_pairs_hook=unique_keys)
        return LabPolicy.model_validate_json(raw)
    except (ValueError, ValidationError):
        return None

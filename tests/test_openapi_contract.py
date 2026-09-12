"""
Unit tests for OpenAPI Schema Contract and Breaking Change Enforcement.

Ensures that:
- Any removed endpoint or method is flagged as breaking.
- Any new required parameter on an existing endpoint is flagged as breaking.
- Changing an optional field to required is flagged as breaking.
- The committed baseline (docs/openapi_snapshot.json) matches current FastAPI endpoints.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_openapi_diff import DEFAULT_SNAPSHOT_PATH, detect_breaking_changes, get_current_openapi


def test_detect_breaking_removed_path():
    baseline = {
        "paths": {
            "/api/office/billing/invoices": {
                "get": {"parameters": [], "responses": {"200": {}}}
            }
        }
    }
    current = {"paths": {}}
    breaking, additions = detect_breaking_changes(baseline, current)
    assert len(breaking) == 1
    assert "REMOVED PATH" in breaking[0]


def test_detect_breaking_removed_method():
    baseline = {
        "paths": {
            "/api/office/billing/invoices": {
                "get": {"parameters": [], "responses": {"200": {}}},
                "post": {"parameters": [], "responses": {"200": {}}},
            }
        }
    }
    current = {
        "paths": {
            "/api/office/billing/invoices": {
                "get": {"parameters": [], "responses": {"200": {}}}
            }
        }
    }
    breaking, additions = detect_breaking_changes(baseline, current)
    assert len(breaking) == 1
    assert "REMOVED OPERATION: POST" in breaking[0]


def test_detect_breaking_optional_param_made_required():
    baseline = {
        "paths": {
            "/api/jobs": {
                "get": {
                    "parameters": [{"name": "status", "in": "query", "required": False}],
                    "responses": {"200": {}},
                }
            }
        }
    }
    current = {
        "paths": {
            "/api/jobs": {
                "get": {
                    "parameters": [{"name": "status", "in": "query", "required": True}],
                    "responses": {"200": {}},
                }
            }
        }
    }
    breaking, additions = detect_breaking_changes(baseline, current)
    assert len(breaking) == 1
    assert "PARAM REQUIRED BREAK" in breaking[0]


def test_detect_breaking_schema_field_removed_or_changed():
    baseline = {
        "paths": {},
        "components": {
            "schemas": {
                "JobResponse": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "contract_sum": {"type": "integer"},
                    },
                    "required": ["job_id"],
                }
            }
        },
    }
    current = {
        "paths": {},
        "components": {
            "schemas": {
                "JobResponse": {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        # contract_sum was removed!
                    },
                    "required": ["job_id"],
                }
            }
        },
    }
    breaking, additions = detect_breaking_changes(baseline, current)
    assert len(breaking) == 1
    assert "REMOVED PROPERTY: Field 'contract_sum'" in breaking[0]


def test_active_repo_schema_matches_snapshot():
    assert DEFAULT_SNAPSHOT_PATH.exists(), "docs/openapi_snapshot.json must exist in repository"
    with open(DEFAULT_SNAPSHOT_PATH, "r", encoding="utf-8") as f:
        baseline = json.load(f)

    current = get_current_openapi()
    breaking, additions = detect_breaking_changes(baseline, current)
    assert (
        len(breaking) == 0
    ), f"OpenAPI contract regressions detected against docs/openapi_snapshot.json:\n" + "\n".join(breaking)

#!/usr/bin/env python3
"""
FastAPI OpenAPI Schema Contract & Breaking Change Enforcement Tool.

Used in CI and local verification to prevent API regressions and breaking changes
between published releases. Compares current FastAPI OpenAPI spec against the baseline
stored in docs/openapi_snapshot.json.

Exit codes:
  0: Schemas are identical or contain only non-breaking additions (new endpoints, optional fields).
  1: Breaking changes detected (removed endpoints, renamed/removed fields, optional made required).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Default snapshot path
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT_PATH = REPO_ROOT / "docs" / "openapi_snapshot.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def get_current_openapi() -> dict[str, Any]:
    """Import FastAPI application and extract OpenAPI schema."""
    from app.server import app

    return app.openapi()


def detect_breaking_changes(
    baseline: dict[str, Any], current: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """
    Compare baseline and current OpenAPI schemas.
    Returns: (breaking_changes, non_breaking_additions)
    """
    breaking: list[str] = []
    additions: list[str] = []

    baseline_paths = baseline.get("paths", {})
    current_paths = current.get("paths", {})

    # 1. Check for removed paths
    for path, path_item in baseline_paths.items():
        if path not in current_paths:
            breaking.append(f"REMOVED PATH: Endpoint '{path}' was completely removed.")
            continue

        curr_path_item = current_paths[path]

        # 2. Check for removed HTTP methods on existing path
        for method, op in path_item.items():
            if method.lower() in ("parameters", "summary", "description"):
                continue
            if method not in curr_path_item:
                breaking.append(
                    f"REMOVED OPERATION: {method.upper()} '{path}' was removed."
                )
                continue

            curr_op = curr_path_item[method]

            # 3. Check parameters
            base_params = {p.get("name"): p for p in op.get("parameters", []) if "name" in p}
            curr_params = {p.get("name"): p for p in curr_op.get("parameters", []) if "name" in p}

            for p_name, p_spec in base_params.items():
                if p_name not in curr_params:
                    if p_spec.get("required", False):
                        breaking.append(
                            f"REMOVED PARAMETER: Required parameter '{p_name}' removed from {method.upper()} '{path}'."
                        )
                    else:
                        additions.append(
                            f"Removed optional parameter '{p_name}' from {method.upper()} '{path}'."
                        )
                else:
                    curr_p = curr_params[p_name]
                    # Optional parameter became required
                    if not p_spec.get("required", False) and curr_p.get("required", False):
                        breaking.append(
                            f"PARAM REQUIRED BREAK: Parameter '{p_name}' on {method.upper()} '{path}' changed from optional to required."
                        )

            # New required parameters added to existing operation
            for p_name, curr_p in curr_params.items():
                if p_name not in base_params and curr_p.get("required", False):
                    breaking.append(
                        f"NEW REQUIRED PARAM: New required parameter '{p_name}' added to existing {method.upper()} '{path}'."
                    )

    # 4. Check for newly added paths (non-breaking)
    for path, path_item in current_paths.items():
        if path not in baseline_paths:
            additions.append(f"NEW PATH: Endpoint '{path}' added.")
        else:
            for method in path_item:
                if method.lower() in ("parameters", "summary", "description"):
                    continue
                if method not in baseline_paths[path]:
                    additions.append(f"NEW OPERATION: {method.upper()} '{path}' added.")

    # 5. Component Schemas diff
    base_schemas = baseline.get("components", {}).get("schemas", {})
    curr_schemas = current.get("components", {}).get("schemas", {})

    for s_name, s_def in base_schemas.items():
        if s_name not in curr_schemas:
            breaking.append(f"REMOVED SCHEMA: Component schema '{s_name}' was removed.")
            continue

        curr_s_def = curr_schemas[s_name]
        base_props = s_def.get("properties", {})
        curr_props = curr_s_def.get("properties", {})
        base_required = set(s_def.get("required", []))
        curr_required = set(curr_s_def.get("required", []))

        # Check removed properties
        for prop_name, prop_spec in base_props.items():
            if prop_name not in curr_props:
                breaking.append(
                    f"REMOVED PROPERTY: Field '{prop_name}' removed from schema '{s_name}'."
                )
            else:
                # Check type mismatch
                base_type = prop_spec.get("type")
                curr_type = curr_props[prop_name].get("type")
                if base_type and curr_type and base_type != curr_type:
                    breaking.append(
                        f"TYPE CHANGED: Field '{prop_name}' on '{s_name}' changed type from '{base_type}' to '{curr_type}'."
                    )

        # Check newly required properties
        for req_prop in curr_required:
            if req_prop not in base_required and req_prop in base_props:
                breaking.append(
                    f"FIELD REQUIRED BREAK: Field '{req_prop}' on schema '{s_name}' was made required (previously optional)."
                )

    return breaking, additions


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAPI Contract & Breaking Change Enforcement")
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT_PATH,
        help=f"Path to baseline OpenAPI JSON snapshot (default: {DEFAULT_SNAPSHOT_PATH})",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Update snapshot file with current OpenAPI schema from app.server:app",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Display all non-breaking additions in addition to breaking changes",
    )
    args = parser.parse_args()

    snapshot_path: Path = args.snapshot
    current_schema = get_current_openapi()

    if args.update or not snapshot_path.exists():
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(current_schema, f, indent=2, sort_keys=True)
        print(f"Successfully generated OpenAPI snapshot at {snapshot_path}")
        print(f"Total endpoints documented: {len(current_schema.get('paths', {}))}")
        return 0

    with open(snapshot_path, "r", encoding="utf-8") as f:
        baseline_schema = json.load(f)

    breaking, additions = detect_breaking_changes(baseline_schema, current_schema)

    if additions and args.verbose:
        print(f"=== Non-Breaking Additions Detected ({len(additions)}) ===")
        for add in additions:
            print(f"  [+] {add}")

    if breaking:
        print("=" * 70, file=sys.stderr)
        print(f"ERROR: {len(breaking)} BREAKING OPENAPI CONTRACT CHANGE(S) DETECTED!", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        for err in breaking:
            print(f"  [!] {err}", file=sys.stderr)
        print(
            "\nIf this breaking change is intentional, update the baseline snapshot via:\n"
            "  python scripts/check_openapi_diff.py --update\n"
            "and commit docs/openapi_snapshot.json in the same commit.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: OpenAPI contract verified against {snapshot_path.name}. Zero breaking changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Evidence-gated Kodi-Wulf package state (never infer runtime health from age)."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

STATES = {"working", "not working"}
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def load_status(root: Path) -> dict[tuple[str, str], dict[str, str]]:
    """Read reviewed decisions; an empty registry means *unverified*, not working."""
    path = root / "_data" / "addon_status.json"
    if not path.is_file():
        return {}
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(f"Unsupported Kodi-Wulf status registry: {path}")
    records = payload.get("packages")
    if not isinstance(records, list):
        raise ValueError("Kodi-Wulf status registry packages must be a list")
    result: dict[tuple[str, str], dict[str, str]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Package status entry must be an object")
        fields = ("addon_id", "version", "sha256", "status", "reason", "evidence", "checked_at")
        if any(not isinstance(record.get(field), str) or not record[field].strip() for field in fields):
            raise ValueError(f"Incomplete status evidence in {path}")
        if record["status"] not in STATES:
            raise ValueError(f"Unsupported status: {record['status']}")
        if SHA256.fullmatch(record["sha256"]) is None:
            raise ValueError(f"Invalid SHA-256 for {record['addon_id']}")
        try:
            date.fromisoformat(record["checked_at"])
        except ValueError as exc:
            raise ValueError(f"Invalid checked_at for {record['addon_id']}") from exc
        key = (record["addon_id"], record["version"])
        if key in result:
            raise ValueError(f"Duplicate package status: {key}")
        result[key] = {field: record[field] for field in fields}
    return result


def status_for(package: Path, addon_id: str, version: str,
               decisions: dict[tuple[str, str], dict[str, str]]) -> str:
    """Return unverified unless exactly this add-on version/hash was reviewed."""
    record = decisions.get((addon_id, version))
    if record is None:
        return "unverified"
    sha256 = hashlib.sha256(package.read_bytes()).hexdigest()
    if sha256 != record["sha256"]:
        raise ValueError(
            f"Status evidence SHA-256 mismatch for {addon_id} {version}; "
            "review the changed ZIP before classifying"
        )
    return record["status"]

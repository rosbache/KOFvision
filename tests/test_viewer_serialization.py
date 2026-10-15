from __future__ import annotations

from kofvision.viewer_serialization import (
    PROJECT_TYPE,
    PROJECT_VERSION,
    ProjectPayloadError,
    validate_project_payload,
)


def test_validate_project_payload_accepts_valid_payload() -> None:
    payload = {
        "type": PROJECT_TYPE,
        "version": PROJECT_VERSION,
        "source_files": [],
    }
    assert validate_project_payload(payload) is payload


def test_validate_project_payload_rejects_wrong_type() -> None:
    payload = {
        "type": "other.project",
        "version": PROJECT_VERSION,
    }
    try:
        validate_project_payload(payload)
    except ProjectPayloadError as exc:
        assert "Invalid project file type" in str(exc)
    else:
        raise AssertionError("Expected ProjectPayloadError")


def test_validate_project_payload_rejects_wrong_version() -> None:
    payload = {
        "type": PROJECT_TYPE,
        "version": 999,
    }
    try:
        validate_project_payload(payload)
    except ProjectPayloadError as exc:
        assert "Unsupported project version" in str(exc)
    else:
        raise AssertionError("Expected ProjectPayloadError")


def test_validate_project_payload_migrates_v1_payload() -> None:
    payload = {
        "type": PROJECT_TYPE,
        "version": 1,
        "source_files": [],
    }
    validated = validate_project_payload(payload)
    assert validated["version"] == PROJECT_VERSION
    assert "volume_history" in validated
    assert "saved_preferences" in validated

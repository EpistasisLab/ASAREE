"""Command policy and download capabilities, without runtime infrastructure."""

import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from asaree.api.result_jobs import Command
from asaree.services import result_exports
from asaree.services.result_jobs import eligible


def test_pending_and_failed_work_never_implicitly_reruns_completed_or_active_slots():
    for status in ("not_started", "failed", "cancelled", "limit_reached"):
        assert eligible(status, "slot", set())
    for status in ("pending", "running", "finalizing", "completed"):
        assert not eligible(status, "slot", set())
    assert eligible("completed", "slot", {"slot"})
    assert not eligible("completed", "another-slot", {"slot"})
    assert not eligible("running", "slot", {"slot"})


def test_command_selections_are_bounded_and_use_stable_ids():
    with pytest.raises(ValidationError):
        Command(kind="plan", request_key="key", replicate_ids=[uuid.uuid4() for _ in range(101)])
    with pytest.raises(ValidationError):
        Command(kind="plan", request_key="key", rerun_slot_ids=["ambiguous-label"])
    with pytest.raises(ValidationError):
        Command(kind="execute-immediately", request_key="key")


def test_download_capability_is_signed_and_bound_to_job_and_owner(monkeypatch):
    monkeypatch.setattr(
        result_exports,
        "get_settings",
        lambda: SimpleNamespace(auth_secret_key="test-secret-key-at-least-32-characters"),
    )
    job = SimpleNamespace(id=uuid.uuid4(), owner_id=uuid.uuid4())
    token = result_exports.download_token(job)
    assert result_exports.validate_download_token(token, job.id) == job.owner_id
    with pytest.raises(ValueError):
        result_exports.validate_download_token(token, uuid.uuid4())
    with pytest.raises(ValueError):
        result_exports.validate_download_token(token + "tampered", job.id)

from datetime import UTC, datetime, timedelta

import pytest

from image_factory.artifacts import build_provenance


def test_local_build_records_utc_time_without_claiming_a_ci_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    before = datetime.now(UTC)
    record = build_provenance().model_dump(mode="json")
    built_at = datetime.fromisoformat(record["built_at"])
    assert before <= built_at <= datetime.now(UTC)
    assert built_at.utcoffset() == timedelta(0)
    assert record["schema_version"] == 1
    assert record["build_url"] is None


def test_ci_manifest_links_to_the_exact_build_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "cdelgehier/image-factory")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    record = build_provenance().model_dump(mode="json")
    assert record["build_url"] == (
        "https://github.com/cdelgehier/image-factory/actions/runs/12345/attempts/2"
    )


@pytest.mark.parametrize("run_id", ["", "../other", "not-a-run"])
def test_ci_build_rejects_an_invalid_run_reference(
    monkeypatch: pytest.MonkeyPatch, run_id: str
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "cdelgehier/image-factory")
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    with pytest.raises(ValueError, match="GitHub build"):
        build_provenance()

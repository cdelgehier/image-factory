from pathlib import Path

import pytest

from image_factory import release
from image_factory.release import require_test_report


def test_accepts_a_successful_vm_report(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        '<testsuites><testsuite tests="6" failures="0" errors="0" skipped="0"/></testsuites>'
    )
    require_test_report(report)


@pytest.mark.parametrize(
    "tests,failures,errors,skipped",
    [
        ("0", "0", "0", "0"),
        ("6", "1", "0", "0"),
        ("6", "0", "1", "0"),
        ("6", "0", "0", "1"),
    ],
)
def test_does_not_publish_without_all_vm_tests(
    tmp_path: Path, tests: str, failures: str, errors: str, skipped: str
) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        f'<testsuites><testsuite tests="{tests}" failures="{failures}" errors="{errors}" skipped="{skipped}"/></testsuites>'
    )
    with pytest.raises(ValueError):
        require_test_report(report)


def test_draft_does_not_change_a_published_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {"v0.1.0": {"draft": False}}
    calls = []
    monkeypatch.setattr(release, "release_record", records.get)
    monkeypatch.setattr(release, "gh", calls.append)
    with pytest.raises(ValueError, match="already published"):
        release.draft("v0.1.0")
    assert calls == []


def test_draft_can_be_retried_without_changing_the_existing_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = {"v0.1.0": {"draft": True}}
    calls = []
    monkeypatch.setattr(release, "release_record", records.get)
    monkeypatch.setattr(release, "gh", calls.append)
    release.draft("v0.1.0")
    assert calls == []

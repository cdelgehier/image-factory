import errno
import io
from collections.abc import Buffer
from pathlib import Path

import pytest

from scripts import zero_free_space


class FakeFullDisk(io.BytesIO):
    """Accept one block, then return a chosen filesystem error."""

    def __init__(self, error: int) -> None:
        super().__init__()
        self.error = error

    def write(self, content: Buffer, /) -> int:
        """Stop before the test fills the real filesystem."""
        if self.tell():
            raise OSError(self.error, "filesystem error")
        return super().write(content)

    def fileno(self) -> int:
        """Return an identifier for the fsync boundary."""
        return 123


def write_one_block(stream: io.BufferedIOBase) -> None:
    """Write a bounded block for the file cleanup test."""
    stream.write(bytes(1024))


def fail_after_one_block(stream: io.BufferedIOBase) -> None:
    """Simulate a disk error after the temporary file was created."""
    stream.write(bytes(1024))
    raise OSError(errno.EIO, "I/O error")


def test_zero_fill_accepts_only_a_full_disk_and_flushes_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = FakeFullDisk(errno.ENOSPC)
    calls: list[int] = []
    monkeypatch.setattr(zero_free_space.os, "fsync", calls.append)
    zero_free_space.write_zeros(stream)
    assert stream.getvalue() == bytes(1024 * 1024)
    assert calls == [123]


def test_zero_fill_does_not_hide_disk_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = FakeFullDisk(errno.EIO)
    calls: list[int] = []
    monkeypatch.setattr(zero_free_space.os, "fsync", calls.append)
    with pytest.raises(OSError) as error:
        zero_free_space.write_zeros(stream)
    assert error.value.errno == errno.EIO


@pytest.mark.parametrize("writer", [write_one_block, fail_after_one_block])
def test_removes_its_zero_file_after_success_or_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: object
) -> None:
    path = tmp_path / "zero"
    monkeypatch.setattr(zero_free_space, "write_zeros", writer)
    if writer is fail_after_one_block:
        with pytest.raises(OSError):
            zero_free_space.zero_free_space(path)
    else:
        zero_free_space.zero_free_space(path)
    assert not path.exists()


def test_preserves_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "zero"
    path.write_text("existing file")
    with pytest.raises(FileExistsError):
        zero_free_space.zero_free_space(path)
    assert path.read_text() == "existing file"

"""Write zeros to free filesystem blocks before disk compression."""

import errno
import os
from pathlib import Path
from typing import BinaryIO


def write_zeros(stream: BinaryIO) -> None:
    """Fill the filesystem and fail on errors other than a full disk."""
    block = bytes(1024 * 1024)
    try:
        while True:
            stream.write(block)
    except OSError as error:
        if error.errno != errno.ENOSPC:
            raise
    # Flush blocks before unlinking the file, so compression sees the zeros.
    os.fsync(stream.fileno())


def zero_free_space(path: Path) -> None:
    """Remove only the temporary file created by this process."""
    stream = path.open("xb", buffering=0)
    try:
        with stream:
            write_zeros(stream)
    finally:
        path.unlink()


if __name__ == "__main__":
    zero_free_space(Path("/image-factory-zero"))

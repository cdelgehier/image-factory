"""Write zeros to free guest filesystem blocks before QCOW2 compression.

Deleted files can leave data in free blocks. Zeroing those blocks lets Packer
compress them without removing files that still belong to the guest.
"""

import errno
import os
from pathlib import Path
from typing import BinaryIO


def write_zeros(stream: BinaryIO) -> None:
    """Fill free blocks and stop only when the filesystem reports a full disk.

    ENOSPC is the expected end of this operation. Other write errors must stop
    the build because they could mean that the disk is damaged or unwritable.
    """

    block = bytes(1024 * 1024)
    try:
        while True:
            stream.write(block)
    except OSError as error:
        if error.errno != errno.ENOSPC:
            raise
    # Complete the writes before unlinking; otherwise old block data may remain.
    os.fsync(stream.fileno())


def zero_free_space(path: Path) -> None:
    """Create a temporary zero file and remove it after success or failure.

    Exclusive creation refuses an existing path. The cleanup must only remove
    a file owned by this operation, even when a write or flush fails.
    """

    # Keep creation outside the try block so a collision never deletes another file.
    stream = path.open("xb", buffering=0)
    try:
        with stream:
            write_zeros(stream)
    finally:
        path.unlink()


if __name__ == "__main__":
    zero_free_space(Path("/image-factory-zero"))

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable

logger = logging.getLogger("hub_server.data_backup")


def backup_files(
    paths: Iterable[str | Path], backup_dir: str | Path, now: datetime
) -> Path | None:
    """Copy every existing source into ``backup_dir/<YYYYmmdd-HHMMSS>/``.

    Returns the created folder, or None if the backup failed. Never raises:
    a broken backup target must not break ending a race.
    """
    # ponytail: no rotation -- the jsonl files are small; add keep-last-N when
    # the backup volume actually fills.
    # ponytail: the backup root must already exist (no parents=True) so an
    # unmounted USB stick whose mount directory is gone fails loudly instead of
    # silently backing up onto the SD card. Ceiling: an existing-but-unmounted
    # mount-point directory still lands on the SD card; upgrade path is an
    # os.path.ismount check on the configured root.
    try:
        dest = Path(backup_dir) / now.strftime("%Y%m%d-%H%M%S")
        dest.mkdir(exist_ok=True)
        for source in paths:
            source = Path(source)
            if source.is_file():
                shutil.copyfile(source, dest / source.name)
        return dest
    except OSError as exc:
        logger.warning("Data backup to %s failed: %s", backup_dir, exc)
        return None

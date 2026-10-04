"""The backup index (backups_metadata.json) and the destination lock."""

import contextlib
import json
import os
from datetime import datetime, timezone

from .errors import BackupError

INDEX_NAME = "backups_metadata.json"
LOCK_NAME = ".pybackup.lock"
INDEX_VERSION = "2.0"


def utc_now():
    return datetime.now(timezone.utc)


def parse_timestamp(value):
    """Parse an ISO timestamp from the index; returns None if it is unusable."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def atomic_write_json(path, data, indent=2):
    """Write JSON so that a crash leaves either the old file or the new one."""
    temp_path = path + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=indent, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


class MetadataStore:
    """Reads and writes the index of one backup destination."""

    def __init__(self, destination_path):
        self.destination = os.path.abspath(destination_path)
        self.path = os.path.join(self.destination, INDEX_NAME)
        self.lock_path = os.path.join(self.destination, LOCK_NAME)

    @staticmethod
    def new_index():
        now = utc_now().isoformat()
        return {"version": INDEX_VERSION, "created": now, "last_modified": now, "backups": []}

    def load(self):
        """Load the index. A missing index is empty; a damaged one is an error.

        Falling back to an empty index here would make the next save erase the
        record of every existing backup.
        """
        if not os.path.exists(self.path):
            return self.new_index()
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                index = json.load(handle)
            if not isinstance(index, dict) or not isinstance(index.get("backups"), list):
                raise ValueError("unexpected structure")
        except (OSError, ValueError) as exc:
            raise BackupError(
                "Backup index is unreadable (%s): %s\n"
                "Nothing was changed and the archives are untouched. "
                "Run 'reindex -d \"%s\"' to rebuild the index from the archives."
                % (exc, self.path, self.destination)
            )
        return index

    def save(self, index):
        index["last_modified"] = utc_now().isoformat()
        index["version"] = INDEX_VERSION
        os.makedirs(self.destination, exist_ok=True)
        try:
            atomic_write_json(self.path, index)
        except OSError as exc:
            raise BackupError("Failed to save backup index %s: %s" % (self.path, exc))

    @staticmethod
    def sorted_backups(index):
        """Backups newest first; entries with unusable timestamps go last."""
        oldest = datetime.min.replace(tzinfo=timezone.utc)
        return sorted(
            index["backups"],
            key=lambda backup: parse_timestamp(backup.get("timestamp")) or oldest,
            reverse=True,
        )

    def find(self, index, reference):
        """Resolve an ID, an archive file name, a unique ID prefix or ``latest``."""
        backups = index["backups"]
        if reference == "latest":
            ordered = self.sorted_backups(index)
            if ordered:
                return ordered[0]
        for key in ("id", "archive_name"):
            for backup in backups:
                if backup.get(key) == reference:
                    return backup
        matches = [b for b in backups if str(b.get("id", "")).startswith(reference)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise BackupError("Backup reference is ambiguous: %s" % reference)
        raise BackupError("Backup not found: %s" % reference)

    @contextlib.contextmanager
    def lock(self):
        """Hold an exclusive lock on the destination while changing it."""
        try:
            os.makedirs(self.destination, exist_ok=True)
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise BackupError(
                "Destination is locked by another run: %s\n"
                "If no other backup is running, delete that file and retry."
                % self.lock_path
            )
        except OSError as exc:
            raise BackupError("Cannot write to destination %s: %s" % (self.destination, exc))
        try:
            with os.fdopen(descriptor, "w") as handle:
                handle.write("pid %d, started %s\n" % (os.getpid(), utc_now().isoformat()))
            yield
        finally:
            try:
                os.remove(self.lock_path)
            except OSError:
                pass

"""High-level backup operations: create, restore, verify, cleanup, reindex."""

import logging
import os
from datetime import datetime, timedelta, timezone

from . import __version__
from .archive import (
    ARCHIVE_SUFFIXES, FORMATS, MANIFEST_NAME, create_archive, extract_archive,
    read_manifest, sha256_file, verify_archive,
)
from .config import (
    GlobalConfig, find_config_file, format_filename, load_project_config, substitute,
)
from .errors import BackupError
from .filters import FileFilter
from .metadata import MetadataStore, parse_timestamp, utc_now

log = logging.getLogger("pybackup")


def format_size(size_bytes):
    """Format a byte count for people."""
    size = float(size_bytes or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return "%d B" % size if unit == "B" else "%.1f %s" % (size, unit)
        size /= 1024.0


def _unique_name(directory, stem, extension):
    """A file name not yet present in ``directory``; existing archives are never replaced."""
    candidate = stem + extension
    number = 2
    while os.path.lexists(os.path.join(directory, candidate)):
        candidate = "%s_%d%s" % (stem, number, extension)
        number += 1
    return candidate


def _archive_format_of(file_name):
    for name in sorted(FORMATS, key=len, reverse=True):
        if file_name.endswith("." + name):
            return name
    return None


class BackupManager:
    """Coordinates scanning, archiving and the backup index."""

    def __init__(self, global_config=None):
        self.config = global_config or GlobalConfig()

    # ------------------------------------------------------------------ backup

    def _collect(self, source_paths, destination, file_filter):
        """Scan all sources and return (files, symlinks, skipped, excluded)."""
        sources = [os.path.abspath(path) for path in source_paths]
        if not sources:
            raise BackupError("No source path given")
        destination_real = os.path.realpath(destination)
        for source in sources:
            if not os.path.exists(source):
                raise BackupError("Source path does not exist: %s" % source)
            if os.path.normcase(os.path.realpath(source)) == os.path.normcase(destination_real):
                raise BackupError(
                    "Destination must not be the source directory itself: %s" % source
                )

        # With several sources each one gets its own top-level folder in the archive.
        prefixes = []
        for source in sources:
            prefix = ""
            if len(sources) > 1:
                base = os.path.basename(source.rstrip("\\/")) or "root"
                prefix, number = base, 2
                while prefix in prefixes:
                    prefix = "%s_%d" % (base, number)
                    number += 1
            prefixes.append(prefix)

        files, symlinks, skipped, excluded = [], [], [], []
        for source, prefix in zip(sources, prefixes):
            lead = prefix + "/" if prefix else ""
            if os.path.isfile(source):
                name = os.path.basename(source)
                files.append((source, name if not prefix else lead + name,
                              os.path.getsize(source)))
                continue
            scan = file_filter.scan(source, skip_dirs=[destination])
            for abs_path, rel, size in scan.files:
                if lead + rel == MANIFEST_NAME:
                    log.warning("Skipping %s: the name is reserved for the archive manifest", rel)
                    continue
                files.append((abs_path, lead + rel, size))
            symlinks.extend((abs_path, lead + rel) for abs_path, rel in scan.symlinks)
            skipped.extend({"path": lead + rel, "error": error} for rel, error in scan.errors)
            excluded.extend(lead + rel for rel in scan.excluded)
            for rel in scan.special:
                log.debug("Ignoring special file: %s", lead + rel)
        return sources, files, symlinks, skipped, excluded

    def create_backup(self, source_paths, destination_path, name=None, summary=None,
                      include_patterns=None, exclude_patterns=None, archive_format=None,
                      compression_level=None, filename_format=None, project_name=None,
                      dry_run=False, verify=False):
        """Create one backup archive.

        Args:
            source_paths (str or list): directories (or files) to back up
            destination_path (str): directory holding archives and the index
            filename_format (str, optional): archive name template; the backup ID
                is used when omitted
            dry_run (bool): only report what would be backed up
            verify (bool): re-read the finished archive and check every hash

        Returns:
            dict: ``id``, ``archive_path``, ``file_count``, ``total_size``,
            ``compressed_size``, ``skipped`` (list), ``partial``, ``files``

        Raises:
            BackupError: nothing was written, or the result cannot be trusted
        """
        if isinstance(source_paths, str):
            source_paths = [source_paths]
        destination = os.path.abspath(destination_path)
        if include_patterns is None:
            include_patterns = self.config.get("default_includes")
        if exclude_patterns is None:
            exclude_patterns = self.config.get("default_excludes")
        archive_format = archive_format or self.config.get("archive_format", "tar.gz")
        if archive_format not in FORMATS:
            raise BackupError("Unsupported archive format: %s (use one of: %s)"
                              % (archive_format, ", ".join(FORMATS)))
        if compression_level is None:
            compression_level = self.config.get("compression_level", 6)

        log.info("Scanning files...")
        file_filter = FileFilter(include_patterns, exclude_patterns)
        sources, files, symlinks, skipped, excluded = self._collect(
            source_paths, destination, file_filter
        )
        for path in excluded:
            log.debug("Excluded: %s", path)
        secrets = [p for p in excluded if p.rsplit("/", 1)[-1].startswith(".env")]
        if excluded:
            log.info("Excluded by patterns: %d entries%s", len(excluded),
                     " (including %d .env file(s), which are NOT in this backup)"
                     % len(secrets) if secrets else "")
        if not files and not symlinks:
            raise BackupError("No files found to backup")

        if dry_run:
            total = sum(size for _path, _rel, size in files)
            log.info("Dry run: %d files, %s would be backed up. Nothing was written.",
                     len(files), format_size(total))
            return {
                "id": None, "dry_run": True, "archive_path": None,
                "file_count": len(files), "total_size": total, "compressed_size": 0,
                "skipped": skipped, "partial": bool(skipped),
                "files": [rel for _path, rel, _size in files],
            }

        now = datetime.now()
        backup_id = "backup_%s_%s" % (now.strftime("%Y%m%d_%H%M%S"), os.urandom(3).hex())
        timestamp = utc_now().isoformat()
        project_name = project_name or os.path.basename(sources[0].rstrip("\\/")) or "backup"
        store = MetadataStore(destination)

        with store.lock():
            index = store.load()
            counters = [b.get("counter") or 0 for b in index["backups"]]
            counter = max(counters + [len(counters)]) + 1
            if filename_format:
                source_dir = sources[0] if os.path.isdir(sources[0]) else os.path.dirname(sources[0])
                stem = format_filename(filename_format, counter=counter,
                                       project_name=project_name, source_dir=source_dir, now=now)
            else:
                stem = backup_id
            archive_name = _unique_name(destination, stem, "." + archive_format)
            archive_path = os.path.join(destination, archive_name)

            manifest = {
                "tool_version": __version__,
                "backup": {
                    "id": backup_id, "name": name or backup_id, "summary": summary or "",
                    "timestamp": timestamp, "source_paths": sources, "counter": counter,
                    "include_patterns": list(include_patterns),
                    "exclude_patterns": list(exclude_patterns),
                },
                "skipped": skipped,
            }
            log.info("Creating archive: %s", archive_name)
            try:
                written = create_archive(
                    files, symlinks, archive_path, manifest, archive_format,
                    compression_level, on_file=lambda rel: log.debug("  + %s", rel),
                )
            except OSError as exc:
                raise BackupError("Failed to create archive: %s" % exc)

            stored = manifest["files"]
            skipped = manifest["skipped"]
            total_size = sum(meta["size"] for meta in stored.values())
            entry = {
                "id": backup_id,
                "timestamp": timestamp,
                "name": name or backup_id,
                "summary": summary or "",
                "archive_name": archive_name,
                "archive_format": archive_format,
                "archive_sha256": written["archive_sha256"],
                "source_path": sources[0],
                "source_paths": sources,
                "file_count": len(stored),
                "total_size": total_size,
                "compressed_size": written["archive_size"],
                "counter": counter,
                "partial": bool(skipped),
                "skipped_count": len(skipped),
                "include_patterns": list(include_patterns),
                "exclude_patterns": list(exclude_patterns),
            }
            index["backups"].append(entry)
            try:
                store.save(index)
            except BackupError as exc:
                raise BackupError(
                    "%s\nThe archive was written: %s\nRun 'reindex' to register it."
                    % (exc, archive_path)
                )

        if verify:
            log.info("Verifying archive...")
            check = verify_archive(archive_path, written["archive_sha256"])
            if not check["ok"]:
                raise BackupError(
                    "Archive failed verification and must not be trusted: %s\n  - %s"
                    % (archive_path, "\n  - ".join(check["problems"][:20]))
                )

        for item in skipped:
            log.warning("NOT backed up: %s (%s)", item["path"], item["error"])
        log.info("Backup completed: %s", backup_id)
        log.info("Files: %d, Size: %s -> %s", len(stored), format_size(total_size),
                 format_size(written["archive_size"]))
        return {
            "id": backup_id, "dry_run": False, "archive_path": archive_path,
            "file_count": len(stored), "total_size": total_size,
            "compressed_size": written["archive_size"],
            "skipped": skipped, "partial": bool(skipped), "files": sorted(stored),
        }

    def create_backup_from_config(self, config_file_path=None, name=None, summary=None,
                                  dry_run=False, verify=False):
        """Create a backup as described by a project config file."""
        config_path = config_file_path or find_config_file()
        if not config_path:
            raise BackupError("No configuration file found")
        config = load_project_config(config_path)
        project_name = config.get("project_name") or os.path.basename(config["config_dir"])

        if summary is None:
            template = config["summary"].get("default_summary", "")
            summary = substitute(template, project_name=project_name,
                                 source_dir=config["config_dir"]) if template else ""

        result = self.create_backup(
            source_paths=config["source_paths"],
            destination_path=config["destination"],
            name=name or config["backup_name"],
            summary=summary,
            include_patterns=config["include_patterns"],
            exclude_patterns=config["exclude_patterns"],
            archive_format=config["archive_format"],
            compression_level=config["compression_level"],
            filename_format=config["filename_format"],
            project_name=project_name,
            dry_run=dry_run,
            verify=verify,
        )

        cleanup = config["auto_cleanup"]
        if cleanup.get("enabled") and not dry_run:
            self.auto_cleanup(config["destination"], result, cleanup.get("max_backups"),
                              cleanup.get("max_age_days"), cleanup.get("min_keep") or 1)
        return result

    def auto_cleanup(self, destination_path, backup_result, max_backups=None,
                     max_age_days=None, min_keep=1):
        """Apply a cleanup policy after a backup, but never after an incomplete one."""
        if backup_result["partial"]:
            log.warning("Automatic cleanup skipped because this backup is incomplete")
            return []
        if not max_backups and not max_age_days:
            return []
        return self.cleanup_old_backups(destination_path, max_backups, max_age_days, min_keep)

    # -------------------------------------------------------------- inspection

    def list_backups(self, destination_path):
        """Return the backups of a destination, newest first."""
        store = MetadataStore(destination_path)
        return store.sorted_backups(store.load())

    def _locate(self, backup_ref, destination_path):
        store = MetadataStore(destination_path)
        entry = store.find(store.load(), backup_ref)
        archive_path = os.path.join(store.destination, entry["archive_name"])
        if not os.path.isfile(archive_path):
            raise BackupError("Archive file not found: %s" % archive_path)
        return entry, archive_path

    def verify_backup(self, backup_ref, destination_path):
        """Check the archive hash and every file hash of one backup.

        Returns:
            dict: ``valid``, ``backup_id``, ``files_checked``, ``problems``, ``note``
        """
        entry, archive_path = self._locate(backup_ref, destination_path)
        check = verify_archive(archive_path, entry.get("archive_sha256"), entry.get("checksums"))
        note = ""
        if not check["has_manifest"]:
            note = "no checksums recorded for this backup; only readability was checked"
        return {
            "valid": check["ok"],
            "backup_id": entry["id"],
            "archive_path": archive_path,
            "files_checked": check["files_checked"],
            "size": entry.get("compressed_size", 0),
            "problems": check["problems"],
            "note": note,
        }

    # ----------------------------------------------------------------- restore

    def restore_backup(self, backup_ref, destination_path, target_path, files=None,
                       verify=True, force=False):
        """Restore a backup into ``target_path``.

        Args:
            backup_ref (str): backup ID, unique ID prefix, archive name or ``latest``
            files (list, optional): patterns selecting a subset to restore
            verify (bool): check the archive hash first and every file hash after
            force (bool): allow restoring into a directory that is not empty

        Returns:
            dict: ``restored`` (count), ``warnings``
        """
        entry, archive_path = self._locate(backup_ref, destination_path)
        target = os.path.abspath(target_path)
        if os.path.exists(target) and not os.path.isdir(target):
            raise BackupError("Restore target is not a directory: %s" % target)
        if os.path.isdir(target) and os.listdir(target) and not force:
            raise BackupError(
                "Restore target is not empty: %s\n"
                "Existing files with the same names would be overwritten. "
                "Use --force to restore anyway." % target
            )

        if verify and entry.get("archive_sha256"):
            if sha256_file(archive_path) != entry["archive_sha256"]:
                raise BackupError(
                    "Archive is corrupted (hash differs from backup time), nothing was "
                    "restored: %s\nUse --no-verify to extract whatever is readable."
                    % archive_path
                )

        selector = FileFilter(files, []).is_selected if files else None
        log.info("Restoring backup %s to %s", entry["id"], target)
        result = extract_archive(archive_path, target, selector, verify, entry.get("checksums"))
        for warning in result["warnings"]:
            log.warning("%s", warning)
        if result["problems"]:
            shown = result["problems"][:20]
            more = len(result["problems"]) - len(shown)
            raise BackupError(
                "Restore finished with %d problem(s):\n  - %s%s"
                % (len(result["problems"]), "\n  - ".join(shown),
                   "\n  ... and %d more" % more if more else "")
            )
        if files and not result["restored"]:
            raise BackupError("No file in the backup matches: %s" % ", ".join(files))
        log.info("Restored %d file(s)%s", result["restored"],
                 ", all checksums verified" if verify else "")
        return {"restored": result["restored"], "warnings": result["warnings"]}

    # ------------------------------------------------------------- maintenance

    def add_summary(self, backup_ref, destination_path, summary):
        """Set the summary of an existing backup. Returns the backup ID."""
        store = MetadataStore(destination_path)
        with store.lock():
            index = store.load()
            entry = store.find(index, backup_ref)
            entry["summary"] = summary
            entry["summary_updated"] = utc_now().isoformat()
            store.save(index)
        return entry["id"]

    def cleanup_old_backups(self, destination_path, max_backups=None, max_age_days=None,
                            min_keep=1, dry_run=False):
        """Delete old backups.

        A backup is removed when it is beyond the newest ``max_backups`` or older
        than ``max_age_days``. The newest ``min_keep`` complete backups are always
        kept, so an age limit can never delete everything.

        Returns:
            list: index entries that were (or with ``dry_run`` would be) removed
        """
        if not max_backups and not max_age_days:
            raise BackupError("Cleanup needs a policy: a backup count and/or a maximum age")
        store = MetadataStore(destination_path)
        if not os.path.isdir(store.destination):
            raise BackupError("Backup destination does not exist: %s" % store.destination)

        def select(index):
            ordered = store.sorted_backups(index)
            complete = [b for b in ordered if not b.get("partial")] or ordered
            protected = {id(b) for b in complete[:max(1, min_keep or 1)]}
            cutoff = utc_now() - timedelta(days=max_age_days) if max_age_days else None
            doomed = []
            for position, backup in enumerate(ordered):
                if id(backup) in protected:
                    continue
                when = parse_timestamp(backup.get("timestamp"))
                too_many = bool(max_backups) and position >= max_backups
                too_old = cutoff is not None and when is not None and when < cutoff
                if too_many or too_old:
                    doomed.append(backup)
            return doomed

        if dry_run:
            return select(store.load())

        with store.lock():
            index = store.load()
            removed = []
            for backup in select(index):
                archive_path = os.path.join(store.destination, backup["archive_name"])
                try:
                    if os.path.lexists(archive_path):
                        os.remove(archive_path)
                except OSError as exc:
                    log.warning("Could not remove %s: %s", backup["archive_name"], exc)
                    continue
                removed.append(backup)
            if removed:
                gone = {id(b) for b in removed}
                index["backups"] = [b for b in index["backups"] if id(b) not in gone]
                store.save(index)
                log.info("Cleaned up %d old backup(s)", len(removed))
        return removed

    def reindex(self, destination_path):
        """Rebuild the index from the archives found in the destination.

        Existing entries are kept (with their summaries) as long as their archive
        still exists. A damaged index file is renamed, never deleted.

        Returns:
            dict: ``kept``, ``added``, ``dropped``, ``unreadable`` (lists of names)
        """
        store = MetadataStore(destination_path)
        if not os.path.isdir(store.destination):
            raise BackupError("Backup destination does not exist: %s" % store.destination)

        with store.lock():
            try:
                index = store.load()
            except BackupError:
                aside = _unique_name(
                    store.destination,
                    os.path.basename(store.path) + ".corrupt-" + utc_now().strftime("%Y%m%d%H%M%S"),
                    "",
                )
                os.replace(store.path, os.path.join(store.destination, aside))
                log.warning("Damaged index kept as: %s", aside)
                index = store.new_index()

            on_disk = sorted(
                name for name in os.listdir(store.destination)
                if name.endswith(ARCHIVE_SUFFIXES)
                and os.path.isfile(os.path.join(store.destination, name))
            )
            report = {"kept": [], "added": [], "dropped": [], "unreadable": []}
            entries = []
            known = set()
            for backup in index["backups"]:
                if backup.get("archive_name") in on_disk:
                    entries.append(backup)
                    known.add(backup["archive_name"])
                    report["kept"].append(backup["archive_name"])
                else:
                    report["dropped"].append(backup.get("archive_name") or backup.get("id"))

            for name in on_disk:
                if name in known:
                    continue
                try:
                    entries.append(self._entry_from_archive(store.destination, name))
                    report["added"].append(name)
                except (BackupError, OSError) as exc:
                    log.warning("Cannot index %s: %s", name, exc)
                    report["unreadable"].append(name)

            index["backups"] = entries
            store.save(index)
        return report

    @staticmethod
    def _entry_from_archive(destination, archive_name):
        path = os.path.join(destination, archive_name)
        manifest, count, total = read_manifest(path)
        manifest = manifest or {}
        info = manifest.get("backup", {})
        files = manifest.get("files")
        skipped = manifest.get("skipped", [])
        archive_format = _archive_format_of(archive_name)
        stem = archive_name[:-len(archive_format) - 1]
        modified = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).isoformat()
        sources = info.get("source_paths", [])
        return {
            "id": info.get("id") or stem,
            "timestamp": info.get("timestamp") or modified,
            "name": info.get("name") or stem,
            "summary": info.get("summary", ""),
            "archive_name": archive_name,
            "archive_format": archive_format,
            "archive_sha256": sha256_file(path),
            "source_path": sources[0] if sources else "",
            "source_paths": sources,
            "file_count": len(files) if files is not None else count,
            "total_size": sum(m["size"] for m in files.values()) if files is not None else total,
            "compressed_size": os.path.getsize(path),
            "counter": info.get("counter"),
            "partial": bool(skipped),
            "skipped_count": len(skipped),
            "include_patterns": info.get("include_patterns", []),
            "exclude_patterns": info.get("exclude_patterns", []),
        }

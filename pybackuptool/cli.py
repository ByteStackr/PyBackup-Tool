"""Command line interface."""

import argparse
import json
import logging
import os
import sys

from . import __version__
from .archive import FORMATS
from .config import GlobalConfig, find_config_file, load_project_config, write_sample_config
from .errors import BackupError
from .manager import BackupManager, format_size

log = logging.getLogger("pybackup")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PARTIAL = 2

EPILOG = """
Examples:
  %(prog)s init-config                          create .pybackup.json in this project
  %(prog)s backup                               back up using .pybackup.json
  %(prog)s backup ./src -d ./backups            back up a directory
  %(prog)s backup ./src -d ./backups --dry-run  show what would be backed up
  %(prog)s list -d ./backups
  %(prog)s verify latest -d ./backups
  %(prog)s restore latest ./restored -d ./backups

Exit codes: 0 success, 1 error, 2 backup written but some files could not be read.
"""


class _MaxLevelFilter(logging.Filter):
    def __init__(self, level):
        super().__init__()
        self.level = level

    def filter(self, record):
        return record.levelno < self.level


def _setup_logging(verbose, quiet):
    """Progress goes to stdout, warnings and errors to stderr."""
    logger = logging.getLogger("pybackup")
    logger.handlers[:] = []
    logger.propagate = False
    logger.setLevel(logging.WARNING if quiet else logging.DEBUG if verbose else logging.INFO)

    out = logging.StreamHandler(sys.stdout)
    out.addFilter(_MaxLevelFilter(logging.WARNING))
    out.setFormatter(logging.Formatter("%(message)s"))
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.WARNING)
    err.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(out)
    logger.addHandler(err)


def create_parser():
    def output_options(default):
        parent = argparse.ArgumentParser(add_help=False)
        parent.add_argument("-v", "--verbose", action="store_true", default=default,
                            help="list every file and excluded path")
        parent.add_argument("-q", "--quiet", action="store_true", default=default,
                            help="print warnings and errors only")
        return parent

    parser = argparse.ArgumentParser(
        prog="pybackup-tool",
        description="PyBackup-Tool %s - zero-dependency backup tool for codebases" % __version__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=EPILOG,
        parents=[output_options(False)],
    )
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    # The same flags are accepted after the command; SUPPRESS keeps a flag given
    # before the command from being reset by the subparser default.
    common = [output_options(argparse.SUPPRESS)]
    commands = parser.add_subparsers(dest="command", metavar="command")

    def destination_option(sub):
        sub.add_argument("-d", "--destination",
                         help="backup directory (default: project config, then global setting)")

    backup = commands.add_parser("backup", parents=common, help="create a backup")
    backup.add_argument("source_paths", nargs="*", metavar="source_path",
                        help="directories or files to back up (omit to use .pybackup.json)")
    destination_option(backup)
    backup.add_argument("-n", "--name", help="human-readable backup name")
    backup.add_argument("-s", "--summary", help="backup description")
    backup.add_argument("--config", help="project config file to use")
    backup.add_argument("--include", action="append", metavar="PATTERN",
                        help="include pattern, repeatable (default: everything)")
    backup.add_argument("--exclude", action="append", metavar="PATTERN",
                        help="exclude pattern, repeatable (replaces the default excludes)")
    backup.add_argument("--format", choices=sorted(FORMATS), dest="archive_format",
                        help="archive format (default: tar.gz)")
    backup.add_argument("--level", type=int, choices=range(10), metavar="0-9",
                        help="compression level (default: 6)")
    backup.add_argument("--dry-run", action="store_true",
                        help="show what would be backed up, write nothing")
    backup.add_argument("--verify", action="store_true",
                        help="re-read the archive afterwards and check every file hash")
    backup.add_argument("--allow-partial", action="store_true",
                        help="exit 0 even if some files could not be read")

    listing = commands.add_parser("list", parents=common, help="list backups")
    destination_option(listing)
    listing.add_argument("--format", choices=["table", "json", "simple"], default="table",
                         help="output format")

    restore = commands.add_parser("restore", parents=common, help="restore a backup")
    restore.add_argument("backup_id", help="backup ID, unique ID prefix, archive name or 'latest'")
    restore.add_argument("target_path", help="directory to restore into")
    destination_option(restore)
    restore.add_argument("--only", action="append", metavar="PATTERN",
                         help="restore only matching files, repeatable")
    restore.add_argument("--force", action="store_true",
                         help="restore into a directory that is not empty")
    restore.add_argument("--no-verify", action="store_true",
                         help="skip hash verification")

    summary = commands.add_parser("add-summary", parents=common,
                                  help="set the summary of an existing backup")
    summary.add_argument("backup_id", help="backup ID, prefix, archive name or 'latest'")
    summary.add_argument("summary_text", help="summary text")
    destination_option(summary)

    verify = commands.add_parser("verify", parents=common,
                                 help="check archive and file hashes of a backup")
    verify.add_argument("backup_id", nargs="?",
                        help="backup ID, prefix, archive name or 'latest'")
    verify.add_argument("--all", action="store_true", help="verify every backup")
    destination_option(verify)

    cleanup = commands.add_parser("cleanup", parents=common, help="delete old backups")
    destination_option(cleanup)
    cleanup.add_argument("--max-age", type=int, metavar="DAYS",
                         help="delete backups older than this")
    cleanup.add_argument("--keep-count", type=int, metavar="N",
                         help="keep only the newest N backups")
    cleanup.add_argument("--min-keep", type=int, default=1, metavar="N",
                         help="never go below N complete backups (default: 1)")
    cleanup.add_argument("--dry-run", action="store_true",
                         help="show what would be deleted, delete nothing")

    reindex = commands.add_parser("reindex", parents=common,
                                  help="rebuild the backup index from the archives")
    destination_option(reindex)

    configure = commands.add_parser("configure", parents=common,
                                    help="show or change global settings")
    configure.add_argument("--default-destination", help="default backup directory")
    configure.add_argument("--max-backups", type=int,
                           help="backups to keep when automatic cleanup is on")
    configure.add_argument("--auto-cleanup", choices=["on", "off"],
                           help="delete old backups after each backup made without a project config")
    configure.add_argument("--compression-level", type=int, choices=range(10), metavar="0-9",
                           help="default compression level")

    init = commands.add_parser("init-config", parents=common,
                               help="create a sample project config")
    init.add_argument("--output", default=".pybackup.json", help="file to create")
    init.add_argument("--force", action="store_true", help="replace an existing file")

    return parser


class CLI:
    """Maps parsed arguments to BackupManager calls. Each handler returns an exit code."""

    def __init__(self):
        self.settings = GlobalConfig()
        self.manager = BackupManager(self.settings)

    def _destination(self, args):
        """-d, else the project config found from the current directory, else the global default."""
        if args.destination:
            return args.destination
        config_path = find_config_file()
        if config_path:
            return load_project_config(config_path)["destination"]
        if self.settings.get("default_destination"):
            return self.settings.get("default_destination")
        raise BackupError(
            "No backup destination: use -d, a .pybackup.json, or "
            "'configure --default-destination'"
        )

    def backup(self, args):
        if args.config or not args.source_paths:
            config_path = args.config or find_config_file()
            if not config_path:
                raise BackupError(
                    "No source path given and no .pybackup.json found.\n"
                    "Run 'init-config' to create one, or pass a source path and -d."
                )
            if args.source_paths or args.destination or args.include or args.exclude \
                    or args.archive_format or args.level is not None:
                raise BackupError(
                    "Source paths, -d, --include, --exclude, --format and --level cannot be "
                    "combined with a project config; edit %s instead." % config_path
                )
            log.info("Using config file: %s", config_path)
            result = self.manager.create_backup_from_config(
                config_path, name=args.name, summary=args.summary,
                dry_run=args.dry_run, verify=args.verify,
            )
        else:
            destination = args.destination or self.settings.get("default_destination")
            if not destination:
                raise BackupError(
                    "No destination specified. Use -d or 'configure --default-destination'."
                )
            result = self.manager.create_backup(
                args.source_paths, destination, name=args.name, summary=args.summary,
                include_patterns=args.include, exclude_patterns=args.exclude,
                archive_format=args.archive_format, compression_level=args.level,
                dry_run=args.dry_run, verify=args.verify,
            )
            if self.settings.get("auto_cleanup") and not args.dry_run:
                self.manager.auto_cleanup(
                    destination, result, self.settings.get("max_backups"),
                    self.settings.get("max_age_days"),
                )

        if args.dry_run:
            for path in result["files"]:
                log.debug("  %s", path)
            return EXIT_OK
        if result["partial"]:
            log.warning(
                "Backup %s is INCOMPLETE: %d file(s) could not be backed up.",
                result["id"], len(result["skipped"]),
            )
            log.info("Backup created with errors: %s", result["id"])
            return EXIT_OK if args.allow_partial else EXIT_PARTIAL
        log.info("Backup created successfully: %s", result["id"])
        return EXIT_OK

    def list(self, args):
        backups = self.manager.list_backups(self._destination(args))
        if args.format == "json":
            print(json.dumps(backups, indent=2, ensure_ascii=False))
        elif args.format == "simple":
            for backup in backups:
                print("%s - %s (%s)" % (backup["id"], backup.get("name", "Unnamed"),
                                        backup.get("timestamp", "Unknown time")))
        elif not backups:
            print("No backups found.")
        else:
            print("Found %d backup(s), newest first:" % len(backups))
            for backup in backups:
                print("-" * 72)
                print("ID:      %s%s" % (backup["id"],
                                         "  [INCOMPLETE]" if backup.get("partial") else ""))
                print("Name:    %s" % backup.get("name", "Unnamed"))
                print("Date:    %s UTC" % str(backup.get("timestamp", "Unknown"))[:19].replace("T", " "))
                print("Archive: %s" % backup.get("archive_name", ""))
                print("Files:   %d, %s -> %s" % (
                    backup.get("file_count", 0), format_size(backup.get("total_size", 0)),
                    format_size(backup.get("compressed_size", 0))))
                if backup.get("summary"):
                    print("Summary: %s" % backup["summary"])
            print("-" * 72)
        return EXIT_OK

    def restore(self, args):
        result = self.manager.restore_backup(
            args.backup_id, self._destination(args), args.target_path,
            files=args.only, verify=not args.no_verify, force=args.force,
        )
        log.info("Restore completed: %d file(s) in %s", result["restored"], args.target_path)
        return EXIT_OK

    def add_summary(self, args):
        backup_id = self.manager.add_summary(args.backup_id, self._destination(args),
                                             args.summary_text)
        log.info("Summary added to backup: %s", backup_id)
        return EXIT_OK

    def verify(self, args):
        destination = self._destination(args)
        if args.all:
            references = [b["id"] for b in self.manager.list_backups(destination)]
            if not references:
                log.info("No backups found.")
                return EXIT_OK
        elif args.backup_id:
            references = [args.backup_id]
        else:
            raise BackupError("Give a backup ID (or 'latest'), or use --all")

        failed = 0
        for reference in references:
            try:
                result = self.manager.verify_backup(reference, destination)
            except BackupError as exc:
                result = {"valid": False, "backup_id": reference, "problems": [str(exc)],
                          "files_checked": 0, "size": 0, "note": ""}
            if result["valid"]:
                log.info("Backup is valid: %s (%d files, %s)%s", result["backup_id"],
                         result["files_checked"], format_size(result["size"]),
                         " - " + result["note"] if result["note"] else "")
            else:
                failed += 1
                log.error("Backup verification FAILED: %s\n  - %s", result["backup_id"],
                          "\n  - ".join(result["problems"][:20]))
        return EXIT_ERROR if failed else EXIT_OK

    def cleanup(self, args):
        if args.keep_count is not None and args.keep_count < 1:
            raise BackupError("--keep-count must be at least 1")
        if args.max_age is not None and args.max_age < 1:
            raise BackupError("--max-age must be at least 1")
        if not args.keep_count and not args.max_age:
            raise BackupError("Nothing to do: give --keep-count and/or --max-age")
        removed = self.manager.cleanup_old_backups(
            self._destination(args), max_backups=args.keep_count, max_age_days=args.max_age,
            min_keep=args.min_keep, dry_run=args.dry_run,
        )
        if not removed:
            log.info("No backups to clean up")
            return EXIT_OK
        log.info("%s %d backup(s):", "Would delete" if args.dry_run else "Deleted", len(removed))
        for backup in removed:
            log.info("  - %s  %s  (%s)", backup["id"], backup.get("archive_name", ""),
                     str(backup.get("timestamp", ""))[:10])
        return EXIT_OK

    def reindex(self, args):
        report = self.manager.reindex(self._destination(args))
        log.info("Index rebuilt: %d kept, %d added from archives, %d dropped (archive gone), "
                 "%d unreadable", len(report["kept"]), len(report["added"]),
                 len(report["dropped"]), len(report["unreadable"]))
        return EXIT_ERROR if report["unreadable"] else EXIT_OK

    def configure(self, args):
        changes = {
            "default_destination": os.path.abspath(args.default_destination)
            if args.default_destination else None,
            "max_backups": args.max_backups,
            "auto_cleanup": None if args.auto_cleanup is None else args.auto_cleanup == "on",
            "compression_level": args.compression_level,
        }
        if args.max_backups is not None and args.max_backups < 1:
            raise BackupError("--max-backups must be at least 1")
        changed = False
        for key, value in changes.items():
            if value is not None:
                self.settings.set(key, value)
                changed = True
        log.info("%s (%s):", "Settings saved" if changed else "Current settings",
                 self.settings.path)
        log.info("  Default destination: %s", self.settings.get("default_destination") or "not set")
        log.info("  Compression level:   %s", self.settings.get("compression_level"))
        log.info("  Automatic cleanup:   %s",
                 "on, keep newest %s" % self.settings.get("max_backups")
                 if self.settings.get("auto_cleanup") else "off")
        return EXIT_OK

    def init_config(self, args):
        write_sample_config(args.output, force=args.force)
        log.info("Sample configuration created: %s", args.output)
        log.info("Edit it, then run: pybackup-tool backup")
        return EXIT_OK


def main(argv=None):
    """Entry point. Returns the process exit code."""
    parser = create_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose, args.quiet)
    if not args.command:
        parser.print_help()
        return EXIT_ERROR

    try:
        cli = CLI()
        handler = getattr(cli, args.command.replace("-", "_"))
        return handler(args)
    except BackupError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except KeyboardInterrupt:
        log.error("Cancelled by user")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())

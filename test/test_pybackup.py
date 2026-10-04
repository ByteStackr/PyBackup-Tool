"""Tests for PyBackup-Tool. Run with: python -m unittest discover -s test -v"""

import io
import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pybackuptool import archive, cli  # noqa: E402
from pybackuptool.archive import MANIFEST_NAME, _HashingReader  # noqa: E402
from pybackuptool.config import (  # noqa: E402
    GlobalConfig, format_filename, load_project_config, validate_project_config,
)
from pybackuptool.errors import BackupError  # noqa: E402
from pybackuptool.filters import FileFilter  # noqa: E402
from pybackuptool.manager import BackupManager  # noqa: E402
from pybackuptool.metadata import INDEX_NAME, LOCK_NAME, MetadataStore, utc_now  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def write(path, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(content, bytes) else "w"
    with open(path, mode) as handle:
        handle.write(content)


def read(path):
    with open(path, "rb") as handle:
        return handle.read()


def tree(root):
    """All files below root as {posix relative path: bytes}."""
    found = {}
    for directory, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(directory, name)
            found[os.path.relpath(full, root).replace(os.sep, "/")] = read(full)
    return found


def archive_names(path):
    with tarfile.open(path, "r:*") as tar:
        return sorted(name for name in tar.getnames() if name != MANIFEST_NAME)


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pybackup-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = os.path.join(self.tmp, "project")
        self.dest = os.path.join(self.tmp, "backups")
        patcher = mock.patch.dict(
            os.environ, {"PYBACKUP_CONFIG": os.path.join(self.tmp, "settings.json")}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = BackupManager(GlobalConfig())
        logger = logging.getLogger("pybackup")
        logger.handlers[:] = [logging.NullHandler()]
        logger.propagate = False

    def make_project(self):
        write(os.path.join(self.src, "root.py"), "print('root')")
        write(os.path.join(self.src, "README.md"), "# readme")
        write(os.path.join(self.src, "src", "a.py"), "a = 1")
        write(os.path.join(self.src, "src", "deep", "b.py"), "b = 2")
        write(os.path.join(self.src, "src", "deep", "data.bin"), bytes(range(256)) * 64)
        write(os.path.join(self.src, "node_modules", "pkg", "index.js"), "module")
        write(os.path.join(self.src, "app.log"), "log")
        write(os.path.join(self.src, ".env"), "SECRET=1")

    def index(self):
        with open(os.path.join(self.dest, INDEX_NAME), encoding="utf-8") as handle:
            return json.load(handle)

    def run_cli(self, *argv):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class PatternTests(unittest.TestCase):
    def selected(self, includes, excludes, paths):
        chosen = FileFilter(includes, excludes)
        return [path for path in paths if chosen.is_selected(path)]

    def test_include_patterns_reach_subdirectories(self):
        paths = ["root.py", "src/a.py", "src/deep/b.py", "src/deep/c.txt", "docs/x.md"]
        self.assertEqual(
            self.selected(["src/**/*", "*.py"], [], paths),
            ["root.py", "src/a.py", "src/deep/b.py", "src/deep/c.txt"],
        )
        self.assertEqual(self.selected(["*.py"], [], paths),
                         ["root.py", "src/a.py", "src/deep/b.py"])
        self.assertEqual(self.selected(["docs/"], [], ["docs/x.md", "docs/a/b.md", "x.md"]),
                         ["docs/x.md", "docs/a/b.md"])

    def test_star_does_not_cross_directories_but_double_star_does(self):
        paths = ["docs/a.md", "docs/sub/b.md", "a.md"]
        self.assertEqual(self.selected(["docs/*.md"], [], paths), ["docs/a.md"])
        self.assertEqual(self.selected(["docs/**/*.md"], [], paths),
                         ["docs/a.md", "docs/sub/b.md"])
        self.assertEqual(self.selected(["**/b.md"], [], paths), ["docs/sub/b.md"])

    def test_excludes_win_and_apply_at_any_depth(self):
        paths = ["a.py", "a.pyc", "pkg/node_modules/x.js", "node_modules/y.js", "pkg/z.js"]
        self.assertEqual(self.selected(["*"], ["node_modules/", "*.pyc"], paths),
                         ["a.py", "pkg/z.js"])
        # A pattern with a slash is anchored to the source root.
        self.assertEqual(self.selected(["*"], ["node_modules/*"], paths),
                         ["a.py", "a.pyc", "pkg/node_modules/x.js", "pkg/z.js"])

    def test_negation_braces_and_character_classes(self):
        paths = [".env", ".env.local", ".env.example", "a.js", "a.ts", "a.css", "f1.txt", "fa.txt"]
        self.assertEqual(
            self.selected(["*"], [".env", ".env.*", "!.env.example", "*.css"], paths),
            [".env.example", "a.js", "a.ts", "f1.txt", "fa.txt"],
        )
        self.assertEqual(self.selected(["*.{js,ts}"], [], paths), ["a.js", "a.ts"])
        self.assertEqual(self.selected(["f[0-9].txt"], [], paths), ["f1.txt"])

    def test_negation_cannot_reach_into_excluded_directory(self):
        chosen = FileFilter(["*"], [".vscode/", "!.vscode/settings.json"])
        self.assertFalse(chosen.is_selected(".vscode/settings.json"))
        chosen = FileFilter(["*"], [".vscode/*", "!.vscode/settings.json"])
        self.assertTrue(chosen.is_selected(".vscode/settings.json"))
        self.assertFalse(chosen.is_selected(".vscode/other.json"))


class BackupTests(TempDirCase):
    def test_roundtrip_restores_identical_files(self):
        self.make_project()
        result = self.manager.create_backup(self.src, self.dest, name="n", summary="s")
        self.assertFalse(result["partial"])
        self.assertEqual(result["file_count"], 5)

        target = os.path.join(self.tmp, "restored")
        restored = self.manager.restore_backup(result["id"], self.dest, target)
        self.assertEqual(restored["restored"], 5)
        expected = tree(self.src)
        for dropped in (".env", "app.log", "node_modules/pkg/index.js"):
            del expected[dropped]
        self.assertEqual(tree(target), expected)

    def test_scan_matches_config_style_includes(self):
        self.make_project()
        result = self.manager.create_backup(
            self.src, self.dest, include_patterns=["src/**/*", "*.py"], exclude_patterns=["*.bin"]
        )
        self.assertEqual(result["files"], ["root.py", "src/a.py", "src/deep/b.py"])

    def test_destination_inside_source_is_never_backed_up(self):
        self.make_project()
        dest = os.path.join(self.src, "my-backups")
        first = self.manager.create_backup(self.src, dest, exclude_patterns=[])
        second = self.manager.create_backup(self.src, dest, exclude_patterns=[])
        self.assertEqual(first["files"], second["files"])
        self.assertFalse([name for name in archive_names(second["archive_path"])
                          if name.startswith("my-backups")])

    def test_destination_equal_to_source_is_rejected(self):
        self.make_project()
        with self.assertRaises(BackupError):
            self.manager.create_backup(self.src, self.src)

    def test_index_holds_summary_not_per_file_checksums(self):
        self.make_project()
        self.manager.create_backup(self.src, self.dest)
        entry = self.index()["backups"][0]
        self.assertNotIn("checksums", entry)
        self.assertEqual(len(entry["archive_sha256"]), 64)
        self.assertEqual(entry["file_count"], 5)

    def test_compression_level_and_formats(self):
        write(os.path.join(self.src, "big.txt"), "pybackup " * 50000)
        sizes = {}
        for level in (0, 9):
            result = self.manager.create_backup(self.src, self.dest, compression_level=level)
            sizes[level] = result["compressed_size"]
        self.assertGreater(sizes[0], sizes[9] * 10)

        for archive_format in ("tar", "tar.bz2", "tar.xz"):
            result = self.manager.create_backup(self.src, self.dest, archive_format=archive_format)
            self.assertTrue(result["archive_path"].endswith("." + archive_format))
            target = os.path.join(self.tmp, "out-" + archive_format)
            self.manager.restore_backup(result["id"], self.dest, target)
            self.assertEqual(tree(target), tree(self.src))

    def test_multiple_sources_get_their_own_folders(self):
        write(os.path.join(self.tmp, "one", "a.txt"))
        write(os.path.join(self.tmp, "two", "b.txt"))
        write(os.path.join(self.tmp, "single.txt"))
        result = self.manager.create_backup(
            [os.path.join(self.tmp, "one"), os.path.join(self.tmp, "two"),
             os.path.join(self.tmp, "single.txt")], self.dest)
        self.assertEqual(result["files"], ["one/a.txt", "single.txt/single.txt", "two/b.txt"])

    def test_dry_run_writes_nothing(self):
        self.make_project()
        result = self.manager.create_backup(self.src, self.dest, dry_run=True)
        self.assertEqual(result["file_count"], 5)
        self.assertFalse(os.path.exists(self.dest))

    def test_unreadable_file_makes_backup_partial(self):
        self.make_project()
        real_open = open

        def flaky_open(path, *args, **kwargs):
            if str(path).endswith("a.py"):
                raise PermissionError(13, "Permission denied", path)
            return real_open(path, *args, **kwargs)

        with mock.patch.object(archive, "open", flaky_open, create=True):
            result = self.manager.create_backup(self.src, self.dest)
        self.assertTrue(result["partial"])
        self.assertEqual([item["path"] for item in result["skipped"]], ["src/a.py"])
        self.assertNotIn("src/a.py", result["files"])
        self.assertTrue(self.index()["backups"][0]["partial"])

    def test_file_shrinking_while_read_keeps_archive_valid(self):
        reader = _HashingReader(io.BytesIO(b"abc"), 10)
        self.assertEqual(reader.read(4), b"abc\0")
        self.assertEqual(reader.read(100), b"\0" * 6)
        self.assertEqual(reader.read(100), b"")
        self.assertIsNotNone(reader.error)

    def test_failed_archive_leaves_no_partial_file(self):
        self.make_project()
        with mock.patch.object(archive.json, "dumps", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                self.manager.create_backup(self.src, self.dest)
        self.assertEqual(os.listdir(self.dest), [])

    def test_lock_blocks_concurrent_run(self):
        self.make_project()
        write(os.path.join(self.dest, LOCK_NAME), "pid 1")
        with self.assertRaisesRegex(BackupError, "locked"):
            self.manager.create_backup(self.src, self.dest)
        os.remove(os.path.join(self.dest, LOCK_NAME))
        self.manager.create_backup(self.src, self.dest)
        self.assertFalse(os.path.exists(os.path.join(self.dest, LOCK_NAME)))

    def test_symlinks_are_stored_as_links(self):
        self.make_project()
        link = os.path.join(self.src, "link.py")
        try:
            os.symlink("root.py", link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available here")
        result = self.manager.create_backup(self.src, self.dest)
        with tarfile.open(result["archive_path"]) as tar:
            self.assertTrue(tar.getmember("link.py").issym())
        target = os.path.join(self.tmp, "restored")
        self.manager.restore_backup(result["id"], self.dest, target)
        self.assertTrue(os.path.islink(os.path.join(target, "link.py")))


class IndexTests(TempDirCase):
    def test_corrupt_index_is_an_error_and_is_not_overwritten(self):
        self.make_project()
        self.manager.create_backup(self.src, self.dest)
        index_path = os.path.join(self.dest, INDEX_NAME)
        write(index_path, "{broken")
        before = sorted(os.listdir(self.dest))
        with self.assertRaisesRegex(BackupError, "reindex"):
            self.manager.create_backup(self.src, self.dest)
        with self.assertRaises(BackupError):
            self.manager.list_backups(self.dest)
        self.assertEqual(read(index_path), b"{broken")
        self.assertEqual(sorted(os.listdir(self.dest)), before)

    def test_reindex_rebuilds_from_archives(self):
        self.make_project()
        first = self.manager.create_backup(self.src, self.dest, name="first", summary="one")
        second = self.manager.create_backup(self.src, self.dest, name="second")
        write(os.path.join(self.dest, INDEX_NAME), "{broken")

        report = self.manager.reindex(self.dest)
        self.assertEqual(len(report["added"]), 2)
        backups = {b["id"]: b for b in self.manager.list_backups(self.dest)}
        self.assertEqual(set(backups), {first["id"], second["id"]})
        self.assertEqual(backups[first["id"]]["name"], "first")
        self.assertEqual(backups[first["id"]]["summary"], "one")
        self.assertEqual(backups[first["id"]]["file_count"], 5)
        self.assertTrue([n for n in os.listdir(self.dest) if ".corrupt-" in n])
        self.assertTrue(self.manager.verify_backup(first["id"], self.dest)["valid"])

    def test_reference_lookup(self):
        self.make_project()
        first = self.manager.create_backup(self.src, self.dest)
        store = MetadataStore(self.dest)
        index = store.load()
        self.assertEqual(store.find(index, "latest")["id"], first["id"])
        self.assertEqual(store.find(index, first["id"][:-2])["id"], first["id"])
        self.assertEqual(store.find(index, first["id"] + ".tar.gz")["id"], first["id"])
        with self.assertRaises(BackupError):
            store.find(index, "nope")

    def test_add_summary(self):
        self.make_project()
        first = self.manager.create_backup(self.src, self.dest)
        self.manager.add_summary("latest", self.dest, "hello")
        self.assertEqual(self.manager.list_backups(self.dest)[0]["summary"], "hello")
        self.assertEqual(self.manager.list_backups(self.dest)[0]["id"], first["id"])


class RestoreSafetyTests(TempDirCase):
    def register(self, archive_name, **extra):
        store = MetadataStore(self.dest)
        index = store.load()
        entry = {"id": "evil", "archive_name": archive_name,
                 "timestamp": utc_now().isoformat()}
        entry.update(extra)
        index["backups"].append(entry)
        store.save(index)

    def make_archive(self, members):
        """members: list of (TarInfo, bytes or None)"""
        os.makedirs(self.dest, exist_ok=True)
        path = os.path.join(self.dest, "evil.tar.gz")
        with tarfile.open(path, "w:gz") as tar:
            for info, data in members:
                if data is None:
                    tar.addfile(info)
                else:
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
        self.register("evil.tar.gz")
        return path

    def test_parent_directory_member_is_rejected(self):
        self.make_archive([(tarfile.TarInfo("../escaped.txt"), b"pwn")])
        target = os.path.join(self.tmp, "out")
        with self.assertRaisesRegex(BackupError, "Unsafe path"):
            self.manager.restore_backup("evil", self.dest, target)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escaped.txt")))

    def test_absolute_member_is_rejected(self):
        outside = os.path.join(self.tmp, "abs-escaped.txt")
        self.make_archive([(tarfile.TarInfo(outside.replace("\\", "/")), b"pwn")])
        with self.assertRaisesRegex(BackupError, "Unsafe path"):
            self.manager.restore_backup("evil", self.dest, os.path.join(self.tmp, "out"))
        self.assertFalse(os.path.exists(outside))

    def test_escaping_symlink_is_rejected(self):
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../outside"
        self.make_archive([(link, None), (tarfile.TarInfo("link/escaped.txt"), b"pwn")])
        os.makedirs(os.path.join(self.tmp, "outside"))
        with self.assertRaises(BackupError):
            self.manager.restore_backup("evil", self.dest, os.path.join(self.tmp, "out"))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "outside")), [])

    def test_hardlinks_and_devices_are_rejected(self):
        hard = tarfile.TarInfo("hard")
        hard.type = tarfile.LNKTYPE
        hard.linkname = "/etc/passwd"
        self.make_archive([(hard, None)])
        with self.assertRaisesRegex(BackupError, "Unsupported entry"):
            self.manager.restore_backup("evil", self.dest, os.path.join(self.tmp, "out"))

    def test_existing_symlink_in_target_is_not_followed(self):
        outside = os.path.join(self.tmp, "outside")
        target = os.path.join(self.tmp, "out")
        os.makedirs(outside)
        os.makedirs(target)
        try:
            os.symlink(outside, os.path.join(target, "sub"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are not available here")
        self.make_archive([(tarfile.TarInfo("sub/file.txt"), b"data")])
        with self.assertRaisesRegex(BackupError, "outside the restore target"):
            self.manager.restore_backup("evil", self.dest, target, force=True)
        self.assertEqual(os.listdir(outside), [])

    def test_non_empty_target_needs_force(self):
        self.make_project()
        result = self.manager.create_backup(self.src, self.dest)
        target = os.path.join(self.tmp, "out")
        write(os.path.join(target, "root.py"), "precious")
        with self.assertRaisesRegex(BackupError, "not empty"):
            self.manager.restore_backup(result["id"], self.dest, target)
        self.assertEqual(read(os.path.join(target, "root.py")), b"precious")
        self.manager.restore_backup(result["id"], self.dest, target, force=True)
        self.assertEqual(read(os.path.join(target, "root.py")), b"print('root')")

    def test_restore_only_selected_files(self):
        self.make_project()
        result = self.manager.create_backup(self.src, self.dest)
        target = os.path.join(self.tmp, "out")
        self.manager.restore_backup(result["id"], self.dest, target, files=["src/deep/"])
        self.assertEqual(sorted(tree(target)), ["src/deep/b.py", "src/deep/data.bin"])
        with self.assertRaisesRegex(BackupError, "No file"):
            self.manager.restore_backup(result["id"], self.dest,
                                        os.path.join(self.tmp, "out2"), files=["nothing"])


class VerifyTests(TempDirCase):
    def test_verify_passes_then_detects_tampering(self):
        self.make_project()
        result = self.manager.create_backup(self.src, self.dest, verify=True)
        check = self.manager.verify_backup(result["id"], self.dest)
        self.assertTrue(check["valid"])
        self.assertEqual(check["files_checked"], 5)

        with open(result["archive_path"], "r+b") as handle:
            handle.seek(os.path.getsize(result["archive_path"]) // 2)
            handle.write(b"\xff\x00\xff\x00garbage")
        check = self.manager.verify_backup(result["id"], self.dest)
        self.assertFalse(check["valid"])
        with self.assertRaisesRegex(BackupError, "corrupted"):
            self.manager.restore_backup(result["id"], self.dest, os.path.join(self.tmp, "out"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "out")))

    def test_verify_detects_swapped_content_with_valid_archive(self):
        self.make_project()
        result = self.manager.create_backup(self.src, self.dest)
        # Rebuild the archive with one file changed but the original manifest.
        rebuilt = result["archive_path"] + ".new"
        with tarfile.open(result["archive_path"]) as old, tarfile.open(rebuilt, "w:gz") as new:
            for member in old:
                data = old.extractfile(member).read()
                if member.name == "root.py":
                    data = b"print('evil')"
                    member.size = len(data)
                new.addfile(member, io.BytesIO(data))
        os.replace(rebuilt, result["archive_path"])
        check = self.manager.verify_backup(result["id"], self.dest)
        self.assertFalse(check["valid"])
        self.assertIn("checksum mismatch: root.py", check["problems"])
        # Even when the archive hash is not available, per-file hashes catch it.
        store = MetadataStore(self.dest)
        index = store.load()
        del index["backups"][0]["archive_sha256"]
        store.save(index)
        with self.assertRaisesRegex(BackupError, "checksum mismatch: root.py"):
            self.manager.restore_backup(result["id"], self.dest,
                                        os.path.join(self.tmp, "out"), verify=True)

    def test_v1_backups_still_restore_and_verify(self):
        self.make_project()
        os.makedirs(self.dest)
        import hashlib
        with tarfile.open(os.path.join(self.dest, "backup_old.tar.gz"), "w:gz") as tar:
            tar.add(os.path.join(self.src, "root.py"), arcname="root.py")
            tar.add(os.path.join(self.src, "src", "a.py"), arcname="src/a.py")
        v1_index = {
            "backups": [{
                "id": "backup_old", "timestamp": "2025-09-06T14:30:22+00:00",
                "archive_name": "backup_old.tar.gz", "name": "old", "file_count": 2,
                "checksums": {
                    "root.py": hashlib.sha256(b"print('root')").hexdigest(),
                    "src\\a.py": hashlib.sha256(b"a = 1").hexdigest(),
                },
            }],
            "config": {}, "version": "1.0",
        }
        write(os.path.join(self.dest, INDEX_NAME), json.dumps(v1_index))

        self.assertTrue(self.manager.verify_backup("backup_old", self.dest)["valid"])
        target = os.path.join(self.tmp, "out")
        self.manager.restore_backup("backup_old", self.dest, target)
        self.assertEqual(sorted(tree(target)), ["root.py", "src/a.py"])
        # A new backup next to v1 entries keeps them.
        self.manager.create_backup(self.src, self.dest)
        self.assertEqual(len(self.manager.list_backups(self.dest)), 2)


class CleanupTests(TempDirCase):
    def make_backups(self, ages_in_days):
        self.make_project()
        ids = []
        for age in ages_in_days:
            ids.append(self.manager.create_backup(self.src, self.dest)["id"])
        store = MetadataStore(self.dest)
        index = store.load()
        for entry, age in zip(index["backups"], ages_in_days):
            entry["timestamp"] = (utc_now() - timedelta(days=age)).isoformat()
        store.save(index)
        return ids

    def remaining(self):
        return [b["id"] for b in self.manager.list_backups(self.dest)]

    def test_keep_count(self):
        ids = self.make_backups([40, 30, 20, 10])
        removed = self.manager.cleanup_old_backups(self.dest, max_backups=2)
        self.assertEqual(sorted(b["id"] for b in removed), sorted(ids[:2]))
        self.assertEqual(self.remaining(), [ids[3], ids[2]])
        archives = [n for n in os.listdir(self.dest) if n.endswith(".tar.gz")]
        self.assertEqual(len(archives), 2)

    def test_age_limit_never_deletes_the_newest_backup(self):
        ids = self.make_backups([400, 300, 200])
        self.manager.cleanup_old_backups(self.dest, max_age_days=30)
        self.assertEqual(self.remaining(), [ids[2]])

    def test_dry_run_deletes_nothing(self):
        ids = self.make_backups([40, 30, 20])
        removed = self.manager.cleanup_old_backups(self.dest, max_backups=1, dry_run=True)
        self.assertEqual(len(removed), 2)
        self.assertEqual(len(self.remaining()), 3)
        self.assertEqual(len(ids), 3)

    def test_incomplete_backup_does_not_count_as_the_one_to_keep(self):
        ids = self.make_backups([300, 200])
        store = MetadataStore(self.dest)
        index = store.load()
        index["backups"][1]["partial"] = True
        store.save(index)
        self.manager.cleanup_old_backups(self.dest, max_age_days=30)
        self.assertEqual(self.remaining(), [ids[0]])

    def test_no_policy_is_an_error(self):
        self.make_backups([1])
        with self.assertRaises(BackupError):
            self.manager.cleanup_old_backups(self.dest)

    def test_nothing_is_deleted_automatically_by_default(self):
        self.make_project()
        for _ in range(12):
            self.assertEqual(self.run_cli("-q", "backup", self.src, "-d", self.dest)[0], 0)
        self.assertEqual(len(self.remaining()), 12)


class ConfigTests(TempDirCase):
    def write_config(self, **overrides):
        config = {"source_paths": ["."], "destination": "./backups"}
        config.update(overrides)
        path = os.path.join(self.src, ".pybackup.json")
        write(path, json.dumps(config))
        return path

    def test_filename_format_and_counter_are_used(self):
        self.make_project()
        path = self.write_config(filename_format="{project_name}_v{SrNo}", project_name="demo")
        names = []
        for _ in range(3):
            result = self.manager.create_backup_from_config(path)
            names.append(os.path.basename(result["archive_path"]))
        self.assertEqual(names, ["demo_v1.tar.gz", "demo_v2.tar.gz", "demo_v3.tar.gz"])
        # The destination inside the source is not swept into later backups.
        self.assertEqual(archive_names(result["archive_path"]),
                         [".pybackup.json", "README.md", "root.py", "src/a.py",
                          "src/deep/b.py", "src/deep/data.bin"])

    def test_same_name_never_overwrites_an_existing_archive(self):
        self.make_project()
        path = self.write_config(filename_format="fixed")
        first = self.manager.create_backup_from_config(path)
        second = self.manager.create_backup_from_config(path)
        self.assertEqual(os.path.basename(first["archive_path"]), "fixed.tar.gz")
        self.assertEqual(os.path.basename(second["archive_path"]), "fixed_2.tar.gz")
        self.assertTrue(os.path.exists(first["archive_path"]))

    def test_paths_resolve_against_config_file_and_options_apply(self):
        self.make_project()
        path = self.write_config(source_paths=["src"], destination="../elsewhere",
                                 archive_format="tar.xz", compression_level=1,
                                 summary={"default_summary": "Backup of {project_name}: ok"})
        cwd = os.getcwd()
        os.chdir(os.path.join(self.src, "src", "deep"))
        try:
            result = self.manager.create_backup_from_config()
        finally:
            os.chdir(cwd)
        self.assertEqual(os.path.dirname(result["archive_path"]),
                         os.path.join(self.tmp, "elsewhere"))
        self.assertTrue(result["archive_path"].endswith(".tar.xz"))
        self.assertEqual(result["files"], ["a.py", "deep/b.py", "deep/data.bin"])
        entry = self.manager.list_backups(os.path.join(self.tmp, "elsewhere"))[0]
        self.assertEqual(entry["summary"], "Backup of project: ok")
        self.assertTrue(path)

    def test_project_cleanup_policy(self):
        self.make_project()
        path = self.write_config(auto_cleanup={"enabled": True, "max_backups": 2})
        for _ in range(4):
            self.manager.create_backup_from_config(path)
        self.assertEqual(len(self.manager.list_backups(os.path.join(self.src, "backups"))), 2)

        path = self.write_config(destination="./keep-all",
                                 auto_cleanup={"enabled": False, "max_backups": 2})
        for _ in range(4):
            self.manager.create_backup_from_config(path)
        self.assertEqual(len(self.manager.list_backups(os.path.join(self.src, "keep-all"))), 4)

    def test_validation(self):
        self.assertEqual(validate_project_config({}), [])
        errors = validate_project_config({
            "source_paths": [], "include_patterns": "x", "archive_format": "zip",
            "compression_level": 12, "filename_format": "{nope}",
            "auto_cleanup": {"enabled": "yes", "max_backups": 0},
        })
        self.assertEqual(len(errors), 7)
        self.make_project()
        with self.assertRaisesRegex(BackupError, "Invalid JSON"):
            write(os.path.join(self.src, "bad.json"), "{")
            load_project_config(os.path.join(self.src, "bad.json"))

    def test_format_filename_sanitizes(self):
        self.assertEqual(format_filename("a/b:{SrNo}", counter=7), "a_b_7")
        with self.assertRaises(BackupError):
            format_filename("{unknown}")

    def test_shipped_example_config_is_valid(self):
        with open(os.path.join(REPO_ROOT, "example_pybackup.json"), encoding="utf-8") as handle:
            self.assertEqual(validate_project_config(json.load(handle)), [])

    def test_unreadable_settings_file_is_an_error(self):
        write(os.environ["PYBACKUP_CONFIG"], "{")
        with self.assertRaises(BackupError):
            GlobalConfig()


class CliTests(TempDirCase):
    def test_full_cycle_and_exit_codes(self):
        self.make_project()
        code, out, _err = self.run_cli("backup", self.src, "-d", self.dest, "-n", "cli")
        self.assertEqual(code, 0)
        self.assertIn("Backup created successfully: backup_", out)
        self.assertIn(".env file(s), which are NOT in this backup", out)

        code, out, _err = self.run_cli("list", "-d", self.dest, "--format", "json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]["name"], "cli")

        self.assertEqual(self.run_cli("verify", "latest", "-d", self.dest)[0], 0)
        self.assertEqual(self.run_cli("verify", "--all", "-d", self.dest)[0], 0)
        target = os.path.join(self.tmp, "out")
        self.assertEqual(self.run_cli("restore", "latest", target, "-d", self.dest)[0], 0)
        code, _out, err = self.run_cli("restore", "latest", target, "-d", self.dest)
        self.assertEqual(code, 1)
        self.assertIn("not empty", err)
        self.assertEqual(self.run_cli("restore", "missing", target, "-d", self.dest)[0], 1)
        self.assertEqual(self.run_cli("cleanup", "-d", self.dest)[0], 1)
        self.assertEqual(self.run_cli("cleanup", "-d", self.dest, "--keep-count", "5")[0], 0)

    def test_partial_backup_exit_code(self):
        self.make_project()
        real_open = open

        def flaky_open(path, *args, **kwargs):
            if str(path).endswith("a.py"):
                raise PermissionError(13, "Permission denied", path)
            return real_open(path, *args, **kwargs)

        with mock.patch.object(archive, "open", flaky_open, create=True):
            code, _out, err = self.run_cli("backup", self.src, "-d", self.dest)
            self.assertEqual(code, 2)
            self.assertIn("NOT backed up: src/a.py", err)
            code, _out, _err = self.run_cli("backup", self.src, "-d", self.dest, "--allow-partial")
            self.assertEqual(code, 0)

    def test_quiet_and_verbose_flags_work_on_either_side(self):
        self.make_project()
        code, out, _err = self.run_cli("-q", "backup", self.src, "-d", self.dest)
        self.assertEqual((code, out), (0, ""))
        code, out, _err = self.run_cli("backup", self.src, "-d", self.dest, "-q")
        self.assertEqual((code, out), (0, ""))
        code, out, _err = self.run_cli("backup", self.src, "-d", self.dest, "-v", "--dry-run")
        self.assertIn("Excluded: node_modules/", out)
        self.assertIn("src/deep/b.py", out)

    def test_init_config_then_backup_uses_project_destination(self):
        self.make_project()
        cwd = os.getcwd()
        os.chdir(self.src)
        try:
            self.assertEqual(self.run_cli("init-config")[0], 0)
            self.assertEqual(self.run_cli("init-config")[0], 1)
            self.assertEqual(self.run_cli("backup")[0], 0)
            code, out, _err = self.run_cli("list", "--format", "simple")
            self.assertEqual(code, 0)
            self.assertIn("Auto Backup", out)
            self.assertEqual(self.run_cli("backup", "-d", "elsewhere")[0], 1)
        finally:
            os.chdir(cwd)

    def test_configure_persists(self):
        code, out, _err = self.run_cli("configure", "--default-destination", self.dest,
                                       "--auto-cleanup", "on", "--max-backups", "3")
        self.assertEqual(code, 0)
        self.assertIn("keep newest 3", out)
        self.make_project()
        for _ in range(5):
            self.assertEqual(self.run_cli("-q", "backup", self.src)[0], 0)
        self.assertEqual(len(self.manager.list_backups(self.dest)), 3)


if __name__ == "__main__":
    unittest.main()

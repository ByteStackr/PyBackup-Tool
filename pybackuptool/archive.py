"""Archive creation, verification and safe extraction."""

import hashlib
import io
import json
import lzma
import os
import re
import stat
import tarfile
import zlib

from .errors import BackupError

MANIFEST_NAME = ".pybackup-manifest.json"
MANIFEST_VERSION = 1
CHUNK_SIZE = 1024 * 1024

# format name -> (tarfile write mode, name of the compression-level keyword)
FORMATS = {
    "tar.gz": ("w:gz", "compresslevel"),
    "tar.bz2": ("w:bz2", "compresslevel"),
    "tar.xz": ("w:xz", "preset"),
    "tar": ("w", None),
}
ARCHIVE_SUFFIXES = tuple("." + name for name in FORMATS)

_READ_ERRORS = (tarfile.TarError, OSError, EOFError, zlib.error, lzma.LZMAError)


class _HashingWriter:
    """File-like wrapper that hashes and counts everything written through it."""

    def __init__(self, fileobj):
        self._fileobj = fileobj
        self.sha256 = hashlib.sha256()
        self.size = 0

    def write(self, data):
        self.sha256.update(data)
        self.size += len(data)
        return self._fileobj.write(data)

    def flush(self):
        self._fileobj.flush()

    def tell(self):
        return self.size


class _HashingReader:
    """Feeds exactly ``size`` bytes of a file to tarfile while hashing them.

    tarfile writes the member header (with the size) before the data, so a short
    or failing read would corrupt the whole archive. Instead of raising, the
    reader pads with zeros and records the problem in ``error``.
    """

    def __init__(self, fileobj, size):
        self._fileobj = fileobj
        self._remaining = size
        self.sha256 = hashlib.sha256()
        self.error = None

    def read(self, count=-1):
        if count is None or count < 0 or count > self._remaining:
            count = self._remaining
        if count == 0:
            return b""
        data = b""
        if self.error is None:
            try:
                data = self._fileobj.read(count)
            except OSError as exc:
                self.error = "read error: %s" % exc
            else:
                if len(data) < count:
                    self.error = "file changed while it was being read"
        if len(data) < count:
            data += b"\0" * (count - len(data))
        self._remaining -= count
        self.sha256.update(data)
        return data


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_archive(files, symlinks, archive_path, manifest, archive_format="tar.gz",
                   compression_level=6, on_file=None):
    """Write ``files`` and ``symlinks`` into a new archive in a single pass.

    Every file is read once: its bytes are hashed while they are streamed into
    the archive. The manifest (hashes, sizes, skipped files) is stored as the
    last member. The archive is written to a temporary name and renamed into
    place only when complete, so a crash never leaves a truncated archive.

    Args:
        files: iterable of (absolute path, archive path, size)
        symlinks: iterable of (absolute path, archive path)
        manifest: dict with the backup description; ``files``, ``symlinks`` and
            ``skipped`` are filled in here
        on_file: optional callback(archive path) called per stored file

    Returns:
        dict: manifest, plus ``archive_sha256`` and ``archive_size`` of the result
    """
    if archive_format not in FORMATS:
        raise BackupError("Unsupported archive_format: %s" % archive_format)
    mode, level_keyword = FORMATS[archive_format]
    options = {}
    if level_keyword:
        level = int(compression_level)
        if archive_format == "tar.bz2":
            level = max(1, level)
        options[level_keyword] = level

    manifest.setdefault("skipped", [])
    manifest["manifest_version"] = MANIFEST_VERSION
    manifest["files"] = {}
    manifest["symlinks"] = {}

    partial_path = archive_path + ".partial"
    try:
        with open(partial_path, "wb") as raw:
            writer = _HashingWriter(raw)
            with tarfile.open(fileobj=writer, mode=mode, format=tarfile.PAX_FORMAT,
                              **options) as tar:
                for abs_path, arc_name, _size in files:
                    _add_file(tar, abs_path, arc_name, manifest)
                    if on_file:
                        on_file(arc_name)
                for abs_path, arc_name in symlinks:
                    _add_symlink(tar, abs_path, arc_name, manifest)

                payload = json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8")
                info = tarfile.TarInfo(MANIFEST_NAME)
                info.size = len(payload)
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(payload))
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(partial_path, archive_path)
    except BaseException:
        try:
            os.remove(partial_path)
        except OSError:
            pass
        raise

    return {
        "manifest": manifest,
        "archive_sha256": writer.sha256.hexdigest(),
        "archive_size": writer.size,
    }


def _add_file(tar, abs_path, arc_name, manifest):
    try:
        handle = open(abs_path, "rb")
    except OSError as exc:
        manifest["skipped"].append({"path": arc_name, "error": str(exc)})
        return
    with handle:
        file_stat = os.fstat(handle.fileno())
        if not stat.S_ISREG(file_stat.st_mode):
            manifest["skipped"].append({"path": arc_name, "error": "not a regular file"})
            return
        info = tarfile.TarInfo(arc_name)
        info.size = file_stat.st_size
        info.mtime = file_stat.st_mtime
        info.mode = stat.S_IMODE(file_stat.st_mode)
        reader = _HashingReader(handle, file_stat.st_size)
        tar.addfile(info, reader)
    if reader.error:
        # The member is already in the stream; leaving it out of the manifest
        # makes restore discard it.
        manifest["skipped"].append({"path": arc_name, "error": reader.error})
    else:
        manifest["files"][arc_name] = {
            "sha256": reader.sha256.hexdigest(),
            "size": file_stat.st_size,
        }


def _add_symlink(tar, abs_path, arc_name, manifest):
    try:
        target = os.readlink(abs_path)
        link_stat = os.lstat(abs_path)
    except OSError as exc:
        manifest["skipped"].append({"path": arc_name, "error": str(exc)})
        return
    if target.startswith("\\\\?\\"):
        target = target[4:]
    target = target.replace("\\", "/")
    info = tarfile.TarInfo(arc_name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    info.mtime = link_stat.st_mtime
    info.mode = 0o777
    tar.addfile(info)
    manifest["symlinks"][arc_name] = target


def _hash_member(tar, member):
    digest = hashlib.sha256()
    source = tar.extractfile(member)
    for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
        digest.update(chunk)
    return digest.hexdigest()


def normalize_legacy_checksums(checksums):
    """v1 kept per-file checksums in the index, with OS-specific separators."""
    return {
        path.replace("\\", "/"): value
        for path, value in (checksums or {}).items() if value
    }


def _compare(expected, computed):
    """Compare {path: sha256} dicts and return a list of problem descriptions."""
    problems = []
    for path in sorted(expected):
        if path not in computed:
            problems.append("missing from archive: %s" % path)
        elif computed[path] != expected[path]:
            problems.append("checksum mismatch: %s" % path)
    return problems


def _expected_hashes(manifest, legacy_checksums):
    if manifest is not None:
        return {path: meta["sha256"] for path, meta in manifest.get("files", {}).items()}
    if legacy_checksums:
        return normalize_legacy_checksums(legacy_checksums)
    return None


def verify_archive(archive_path, expected_sha256=None, legacy_checksums=None):
    """Read the whole archive and check every file against its recorded hash.

    Returns:
        dict: ``ok``, ``files_checked``, ``problems`` (list of str), ``manifest``
    """
    problems = []
    computed = {}
    manifest = None

    if expected_sha256:
        try:
            if sha256_file(archive_path) != expected_sha256:
                problems.append("archive hash differs from the one recorded at backup time")
        except OSError as exc:
            problems.append("archive unreadable: %s" % exc)

    try:
        with tarfile.open(archive_path, "r:*") as tar:
            for member in tar:
                if not member.isreg():
                    continue
                if member.name == MANIFEST_NAME:
                    manifest = json.load(tar.extractfile(member))
                else:
                    computed[member.name] = _hash_member(tar, member)
    except _READ_ERRORS + (ValueError,) as exc:
        problems.append("archive unreadable: %s" % exc)

    expected = _expected_hashes(manifest, legacy_checksums)
    if expected is not None:
        problems.extend(_compare(expected, computed))
        if manifest is not None:
            unlisted = set(computed) - set(expected)
            unlisted -= {item["path"] for item in manifest.get("skipped", [])}
            problems.extend("not in manifest: %s" % path for path in sorted(unlisted))

    return {
        "ok": not problems,
        "files_checked": len(computed),
        "has_manifest": manifest is not None or bool(legacy_checksums),
        "problems": problems,
        "manifest": manifest,
    }


def read_manifest(archive_path):
    """Return (manifest or None, file count, total size) for an archive."""
    manifest = None
    count = 0
    total = 0
    try:
        with tarfile.open(archive_path, "r:*") as tar:
            for member in tar:
                if not member.isreg():
                    continue
                if member.name == MANIFEST_NAME:
                    manifest = json.load(tar.extractfile(member))
                else:
                    count += 1
                    total += member.size
    except _READ_ERRORS + (ValueError,) as exc:
        raise BackupError("Cannot read archive %s: %s" % (archive_path, exc))
    return manifest, count, total


def _is_within(path, root):
    path = os.path.normcase(path)
    root = os.path.normcase(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _safe_parts(member_name):
    """Split an archive member name into path components, rejecting escapes."""
    name = member_name.replace("\\", "/")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if (
        not parts
        or name.startswith("/")
        or re.match(r"^[A-Za-z]:", name)
        or ".." in parts
        or (os.name == "nt" and any(":" in part for part in parts))
    ):
        raise BackupError("Unsafe path in archive, restore aborted: %r" % member_name)
    return parts


class _Extractor:
    """Extracts members below one root directory and refuses to write outside it."""

    def __init__(self, target_path):
        self.target = os.path.abspath(target_path)
        os.makedirs(self.target, exist_ok=True)
        self.root_real = os.path.realpath(self.target)
        self._checked_dirs = set()

    def _prepare_dir(self, directory):
        """Create ``directory`` after proving it resolves inside the root."""
        if directory in self._checked_dirs:
            return
        existing = directory
        while not os.path.lexists(existing):
            existing = os.path.dirname(existing)
        if not _is_within(os.path.realpath(existing), self.root_real):
            raise BackupError(
                "Refusing to write outside the restore target through: %s" % existing
            )
        os.makedirs(directory, exist_ok=True)
        self._checked_dirs.add(directory)

    def write_file(self, tar, member, parts):
        """Extract one regular file, returning its sha256."""
        destination = os.path.join(self.target, *parts)
        self._prepare_dir(os.path.dirname(destination))
        temp_path = destination + ".pybackup-tmp"
        digest = hashlib.sha256()
        source = tar.extractfile(member)
        try:
            with open(temp_path, "wb") as out:
                for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                    digest.update(chunk)
                    out.write(chunk)
            # Replacing (not opening) the destination never follows a symlink there.
            os.replace(temp_path, destination)
        except BaseException:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            raise
        if os.name != "nt":
            os.chmod(destination, member.mode & 0o777)
        os.utime(destination, (member.mtime, member.mtime))
        return digest.hexdigest()

    def make_dir(self, parts):
        self._prepare_dir(os.path.join(self.target, *parts))

    def make_symlink(self, member, parts):
        """Create a symlink; returns a warning string if the OS refused."""
        link_target = member.linkname
        rel = "/".join(parts)
        resolved = os.path.normpath(
            os.path.join(os.path.dirname(rel), link_target.replace("\\", "/"))
        ).replace("\\", "/")
        if (
            link_target.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", link_target)
            or resolved == ".."
            or resolved.startswith("../")
        ):
            raise BackupError(
                "Unsafe symlink in archive, restore aborted: %s -> %s" % (rel, link_target)
            )
        destination = os.path.join(self.target, *parts)
        self._prepare_dir(os.path.dirname(destination))
        if os.path.lexists(destination):
            if os.path.isdir(destination) and not os.path.islink(destination):
                raise BackupError("Cannot replace directory with symlink: %s" % rel)
            os.remove(destination)
        try:
            os.symlink(link_target, destination)
        except (OSError, NotImplementedError) as exc:
            return "could not create symlink %s -> %s: %s" % (rel, link_target, exc)
        if not _is_within(os.path.realpath(destination), self.root_real):
            os.remove(destination)
            raise BackupError(
                "Unsafe symlink in archive, restore aborted: %s -> %s" % (rel, link_target)
            )
        return None


def extract_archive(archive_path, target_path, selector=None, verify=True,
                    legacy_checksums=None):
    """Extract an archive without ever writing outside ``target_path``.

    Absolute paths, ``..`` components, hard links, device nodes and symlinks
    pointing outside the target abort the restore. File hashes are computed
    while extracting and compared with the manifest at the end.

    Args:
        selector: optional callable(relative path) -> bool to restore a subset
        verify: compare hashes with the manifest (or v1 index checksums)

    Returns:
        dict: ``restored`` (count), ``problems``, ``warnings``
    """
    extractor = _Extractor(target_path)
    computed = {}
    warnings = []
    manifest = None

    try:
        with tarfile.open(archive_path, "r:*") as tar:
            for member in tar:
                if member.name == MANIFEST_NAME and member.isreg():
                    manifest = json.load(tar.extractfile(member))
                    continue
                parts = _safe_parts(member.name)
                rel = "/".join(parts)
                if selector and not selector(rel):
                    continue
                if member.isreg():
                    computed[rel] = extractor.write_file(tar, member, parts)
                elif member.isdir():
                    extractor.make_dir(parts)
                elif member.issym():
                    warning = extractor.make_symlink(member, parts)
                    if warning:
                        warnings.append(warning)
                else:
                    raise BackupError(
                        "Unsupported entry type in archive, restore aborted: %s" % rel
                    )
    except _READ_ERRORS + (ValueError,) as exc:
        raise BackupError("Restore failed: %s" % exc)

    problems = []
    if verify:
        expected = _expected_hashes(manifest, legacy_checksums)
        if expected is None:
            warnings.append("archive has no manifest; restored files were not verified")
        else:
            if selector:
                expected = {p: h for p, h in expected.items() if selector(p)}
            problems.extend(_compare(expected, computed))
            if manifest is not None:
                # Files that changed while they were backed up are in the archive
                # stream but not in the manifest; their content is not trustworthy.
                for rel in sorted(set(computed) - set(expected)):
                    try:
                        os.remove(os.path.join(extractor.target, *rel.split("/")))
                    except OSError:
                        pass
                    del computed[rel]
                    warnings.append("not restored, was not backed up intact: %s" % rel)

    return {"restored": len(computed), "problems": problems, "warnings": warnings}

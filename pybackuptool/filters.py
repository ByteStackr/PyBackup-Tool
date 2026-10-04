"""File selection: gitignore-style include/exclude patterns and directory scanning."""

import os
import re

DEFAULT_EXCLUDES = [
    ".git/", "node_modules/", "__pycache__/",
    "*.pyc", "*.pyo", "*.pyd",
    ".env", ".env.*",
    "*.log", "*.tmp",
    ".DS_Store", "Thumbs.db",
    "*.swp", "*.swo",
    ".vscode/", ".idea/",
]

_WINDOWS_DEVICE_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + ["COM%d" % i for i in range(1, 10)]
    + ["LPT%d" % i for i in range(1, 10)]
)
_REGEX_FLAGS = re.IGNORECASE if os.name == "nt" else 0


def _expand_braces(pattern):
    """Expand ``*.{js,ts}`` into ``["*.js", "*.ts"]``."""
    match = re.search(r"\{([^{}]*,[^{}]*)\}", pattern)
    if not match:
        return [pattern]
    expanded = []
    for alternative in match.group(1).split(","):
        expanded.extend(
            _expand_braces(pattern[:match.start()] + alternative + pattern[match.end():])
        )
    return expanded


def _translate(pattern):
    """Translate one glob into a regex. ``*`` and ``?`` never cross a ``/``; ``**`` does."""
    out = []
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            if pattern.startswith("**/", i):
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            end = pattern.find("]", i + 2)
            if end == -1:
                out.append(re.escape(char))
            else:
                body = pattern[i + 1:end].replace("\\", "\\\\")
                if body[0] in "!^":
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                i = end
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


class Pattern:
    """One include/exclude pattern.

    A pattern containing ``/`` (other than a trailing one) is anchored to the
    source root and matched against the whole relative path. A pattern without
    ``/`` matches a file or directory name at any depth. A trailing ``/``
    restricts the pattern to directories. A leading ``!`` negates it.
    """

    def __init__(self, text):
        raw = str(text).strip().replace("\\", "/")
        self.text = raw
        self.negated = raw.startswith("!")
        if self.negated:
            raw = raw[1:]
        if raw.startswith("./"):
            raw = raw[2:]
        self.dir_only = raw.endswith("/")
        raw = raw.rstrip("/")
        self.anchored = "/" in raw
        raw = raw.lstrip("/")
        self.empty = not raw
        self._regexes = [
            re.compile(_translate(p), _REGEX_FLAGS) for p in _expand_braces(raw)
        ]

    def matches(self, rel_path, is_dir=False):
        if self.empty or (self.dir_only and not is_dir):
            return False
        target = rel_path if self.anchored else rel_path.rsplit("/", 1)[-1]
        return any(regex.fullmatch(target) for regex in self._regexes)


class ScanResult:
    """Outcome of scanning one source directory."""

    def __init__(self):
        self.files = []      # (absolute path, relative posix path, size)
        self.symlinks = []   # (absolute path, relative posix path)
        self.excluded = []   # relative paths removed by exclude patterns
        self.special = []    # relative paths that are not regular files (sockets, devices...)
        self.errors = []     # (relative path, message) for entries that could not be read

    def sort(self):
        self.files.sort(key=lambda item: item[1])
        self.symlinks.sort(key=lambda item: item[1])


class FileFilter:
    """Selects files with include patterns, then removes those hit by exclude patterns.

    Exclusion wins over inclusion. Exclude patterns are evaluated in order and a
    later ``!pattern`` re-includes what an earlier one excluded, except below a
    directory that is itself excluded (same rule as .gitignore).
    """

    def __init__(self, include_patterns=None, exclude_patterns=None):
        if include_patterns is None:
            include_patterns = ["*"]
        if exclude_patterns is None:
            exclude_patterns = DEFAULT_EXCLUDES
        self.include_patterns = list(include_patterns)
        self.exclude_patterns = list(exclude_patterns)
        self._includes = [p for p in map(Pattern, self.include_patterns) if not p.negated]
        self._excludes = [Pattern(p) for p in self.exclude_patterns]

    def _excluded(self, rel_path, is_dir):
        excluded = False
        for pattern in self._excludes:
            if pattern.matches(rel_path, is_dir):
                excluded = not pattern.negated
        return excluded

    def _included(self, rel_path, is_dir):
        return any(pattern.matches(rel_path, is_dir) for pattern in self._includes)

    def is_selected(self, rel_path, is_dir=False):
        """Decide for a single relative path, checking its parent directories too."""
        rel_path = rel_path.replace("\\", "/").strip("/")
        parts = rel_path.split("/")
        inherited = False
        for depth in range(1, len(parts)):
            ancestor = "/".join(parts[:depth])
            if self._excluded(ancestor, True):
                return False
            inherited = inherited or self._included(ancestor, True)
        if self._excluded(rel_path, is_dir):
            return False
        return inherited or self._included(rel_path, is_dir)

    def scan(self, source_path, skip_dirs=()):
        """Walk ``source_path`` and return a ScanResult.

        Excluded directories are never entered. Directories listed in
        ``skip_dirs`` (the backup destination) are skipped whatever the patterns say.
        Symlinks are recorded, not followed.
        """
        source_path = os.path.abspath(source_path)
        source_real = os.path.realpath(source_path)
        skip = {os.path.normcase(os.path.realpath(d)) for d in skip_dirs}
        result = ScanResult()

        # Each stack item: (directory path, relative path, inside an included directory)
        stack = [(source_path, "", False)]
        while stack:
            dir_path, dir_rel, inherited = stack.pop()
            try:
                with os.scandir(dir_path) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name)
            except OSError as exc:
                result.errors.append((dir_rel or ".", str(exc)))
                continue

            for entry in entries:
                rel = dir_rel + "/" + entry.name if dir_rel else entry.name
                try:
                    is_link = entry.is_symlink()
                    is_junction = getattr(entry, "is_junction", lambda: False)()
                    # For a symlink, is_dir only decides whether "dir/" patterns apply.
                    is_dir = entry.is_dir(follow_symlinks=is_link)
                    is_file = not is_link and entry.is_file(follow_symlinks=False)
                    size = entry.stat(follow_symlinks=False).st_size if is_file else 0
                except OSError as exc:
                    result.errors.append((rel, str(exc)))
                    continue

                if is_dir and not is_link and skip:
                    real = os.path.normcase(os.path.join(source_real, *rel.split("/")))
                    if real in skip:
                        result.excluded.append(rel + "/")
                        continue
                if self._excluded(rel, is_dir):
                    result.excluded.append(rel + "/" if is_dir else rel)
                    continue

                selected = inherited or self._included(rel, is_dir)
                if is_link:
                    if selected:
                        result.symlinks.append((entry.path, rel))
                elif is_junction:
                    result.special.append(rel)
                elif is_dir:
                    stack.append((entry.path, rel, selected))
                elif not selected:
                    continue
                elif not is_file or (
                    os.name == "nt"
                    and entry.name.split(".")[0].upper() in _WINDOWS_DEVICE_NAMES
                ):
                    result.special.append(rel)
                else:
                    result.files.append((entry.path, rel, size))

        result.sort()
        return result

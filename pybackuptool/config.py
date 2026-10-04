"""Global settings, per-project config files and archive name formatting."""

import json
import logging
import os
import re
import subprocess
from datetime import datetime

from .archive import FORMATS
from .errors import BackupError
from .filters import DEFAULT_EXCLUDES
from .metadata import atomic_write_json

log = logging.getLogger("pybackup")

PROJECT_CONFIG_NAMES = (".pybackup.json", ".pybackup")
DEFAULT_FILENAME_FORMAT = "{DDMMYY}_Backup_{SrNo}"
FILENAME_VARIABLES = (
    "DDMMYY", "YYMMDD", "YYYY-MM-DD", "HH-MM-SS", "timestamp",
    "SrNo", "counter", "project_name", "branch",
)
# Accepted for v1 compatibility, no longer has any effect.
_DEPRECATED_KEYS = {"generate_checksums": "checksums are always generated"}


class GlobalConfig:
    """User-wide settings stored in ~/.pybackup_config.json."""

    DEFAULTS = {
        "default_destination": "",
        "max_backups": 10,
        "max_age_days": None,
        "compression_level": 6,
        "archive_format": "tar.gz",
        "default_includes": ["*"],
        "default_excludes": DEFAULT_EXCLUDES,
        "auto_cleanup": False,
    }

    def __init__(self, path=None):
        self.path = path or os.environ.get("PYBACKUP_CONFIG") or os.path.join(
            os.path.expanduser("~"), ".pybackup_config.json"
        )
        self.values = self._load()

    def _load(self):
        values = {
            key: (list(value) if isinstance(value, list) else value)
            for key, value in self.DEFAULTS.items()
        }
        if not os.path.exists(self.path):
            return values
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                stored = json.load(handle)
            if not isinstance(stored, dict):
                raise ValueError("expected a JSON object")
        except (OSError, ValueError) as exc:
            raise BackupError(
                "Settings file is unreadable (%s): %s\nFix or delete it." % (exc, self.path)
            )
        values.update(stored)
        return values

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        try:
            atomic_write_json(self.path, self.values)
        except OSError as exc:
            raise BackupError("Could not save settings %s: %s" % (self.path, exc))


def git_branch(directory):
    """Current git branch of ``directory``, or ``no-branch``."""
    try:
        result = subprocess.run(
            ["git", "-C", directory, "rev-parse", "--abbrev-ref", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            universal_newlines=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "no-branch"
    branch = result.stdout.strip()
    return branch if result.returncode == 0 and branch else "no-branch"


def substitute(template, counter=1, project_name="", source_dir=".", now=None):
    """Replace ``{variable}`` placeholders. Unknown variables are an error."""
    now = now or datetime.now()

    def replace(match):
        name = match.group(1)
        if name == "DDMMYY":
            return now.strftime("%d%m%y")
        if name == "YYMMDD":
            return now.strftime("%y%m%d")
        if name == "YYYY-MM-DD":
            return now.strftime("%Y-%m-%d")
        if name == "HH-MM-SS":
            return now.strftime("%H-%M-%S")
        if name == "timestamp":
            return str(int(now.timestamp()))
        if name in ("SrNo", "counter"):
            return str(counter)
        if name == "project_name":
            return project_name
        if name == "branch":
            return git_branch(source_dir)
        raise BackupError(
            "Unknown variable {%s}. Available: %s" % (name, ", ".join(FILENAME_VARIABLES))
        )

    return re.sub(r"\{([^{}]+)\}", replace, template)


def format_filename(template, **variables):
    """Substitute variables, then make the result safe as a file name."""
    name = substitute(template, **variables)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    if not name:
        raise BackupError("filename_format produces an empty file name: %r" % template)
    return name[:200]


def default_project_config():
    return {
        "backup_name": "Auto Backup",
        "source_paths": ["."],
        "destination": "./backups",
        "filename_format": DEFAULT_FILENAME_FORMAT,
        "archive_format": "tar.gz",
        "include_patterns": ["*"],
        "exclude_patterns": list(DEFAULT_EXCLUDES),
        "compression_level": 6,
        "auto_cleanup": {
            "enabled": False,
            "max_backups": 10,
            "max_age_days": None,
            "min_keep": 1,
        },
        "summary": {
            "default_summary": "Automated backup of {project_name}",
        },
    }


def find_config_file(start_path=None):
    """Look for a project config in ``start_path`` and its parents.

    The search stops at the first git repository root or the filesystem root.
    """
    current = os.path.abspath(start_path or os.getcwd())
    while True:
        for name in PROJECT_CONFIG_NAMES:
            candidate = os.path.join(current, name)
            if os.path.isfile(candidate):
                return candidate
        parent = os.path.dirname(current)
        if parent == current or os.path.exists(os.path.join(current, ".git")):
            return None
        current = parent


def _is_string_list(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_positive_int_or_none(value):
    return value is None or (
        isinstance(value, int) and not isinstance(value, bool) and value >= 1
    )


def validate_project_config(config):
    """Return a list of error messages; empty when the config is usable."""
    if not isinstance(config, dict):
        return ["config must be a JSON object"]
    errors = []

    sources = config.get("source_paths", ["."])
    if not _is_string_list(sources) or not sources:
        errors.append("source_paths must be a non-empty list of paths")
    destination = config.get("destination", "./backups")
    if not isinstance(destination, str) or not destination:
        errors.append("destination must be a path")
    for key in ("include_patterns", "exclude_patterns"):
        if key in config and not _is_string_list(config[key]):
            errors.append("%s must be a list of strings" % key)
    if config.get("archive_format", "tar.gz") not in FORMATS:
        errors.append("archive_format must be one of: %s" % ", ".join(FORMATS))
    level = config.get("compression_level", 6)
    if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 9:
        errors.append("compression_level must be an integer from 0 to 9")
    if "filename_format" in config:
        try:
            if not isinstance(config["filename_format"], str):
                raise BackupError("filename_format must be a string")
            # {branch} would call git; validation only needs the syntax.
            format_filename(config["filename_format"].replace("{branch}", "b"))
        except BackupError as exc:
            errors.append(str(exc))

    cleanup = config.get("auto_cleanup", {})
    if not isinstance(cleanup, dict):
        errors.append("auto_cleanup must be an object")
    else:
        if not isinstance(cleanup.get("enabled", False), bool):
            errors.append("auto_cleanup.enabled must be true or false")
        for key in ("max_backups", "max_age_days", "min_keep"):
            if not _is_positive_int_or_none(cleanup.get(key)):
                errors.append("auto_cleanup.%s must be a positive integer or null" % key)
    if not isinstance(config.get("summary", {}), dict):
        errors.append("summary must be an object")
    return errors


def load_project_config(config_path):
    """Load, validate and normalise a project config file.

    Relative ``destination`` and ``source_paths`` are resolved against the
    directory holding the config file, not the current directory.
    """
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except ValueError as exc:
        raise BackupError("Invalid JSON in config file %s: %s" % (config_path, exc))
    except OSError as exc:
        raise BackupError("Cannot read config file %s: %s" % (config_path, exc))

    errors = validate_project_config(config)
    if errors:
        raise BackupError(
            "Invalid configuration in %s:\n  - %s" % (config_path, "\n  - ".join(errors))
        )

    defaults = default_project_config()
    for key in config:
        if key in _DEPRECATED_KEYS:
            log.warning("Config key '%s' is ignored: %s", key, _DEPRECATED_KEYS[key])
        elif key not in defaults and key != "project_name" and not key.startswith("_"):
            log.warning("Unknown config key ignored: %s", key)
    for key, value in defaults.items():
        config.setdefault(key, value)

    config_dir = os.path.dirname(os.path.abspath(config_path))
    config["config_dir"] = config_dir
    config["destination"] = os.path.abspath(os.path.join(config_dir, config["destination"]))
    config["source_paths"] = [
        os.path.abspath(os.path.join(config_dir, path)) for path in config["source_paths"]
    ]
    return config


def write_sample_config(output_path, force=False):
    if os.path.exists(output_path) and not force:
        raise BackupError("%s already exists. Use --force to replace it." % output_path)
    try:
        atomic_write_json(output_path, default_project_config(), indent=4)
    except OSError as exc:
        raise BackupError("Could not write %s: %s" % (output_path, exc))

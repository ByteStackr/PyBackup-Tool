"""PyBackup-Tool: zero-dependency backup tool for codebases."""

__version__ = "2.0.0"

from .errors import BackupError
from .filters import DEFAULT_EXCLUDES, FileFilter
from .manager import BackupManager

__all__ = ["BackupError", "BackupManager", "DEFAULT_EXCLUDES", "FileFilter", "__version__"]

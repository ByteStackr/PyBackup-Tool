#!/usr/bin/env python3
"""
PyBackup_Tool.py - launcher kept for backwards compatibility.

The implementation lives in the ``pybackuptool`` package next to this file.
``python PyBackup_Tool.py <command>`` behaves the same as ``pybackup-tool <command>``.
"""

import sys

from pybackuptool import BackupError, BackupManager, FileFilter, __version__  # noqa: F401
from pybackuptool.cli import main

if __name__ == "__main__":
    sys.exit(main())

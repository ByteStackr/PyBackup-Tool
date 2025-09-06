#!/usr/bin/env python3
"""
PyBackup_Tool.py - Zero Dependencies Backup Tool
A robust backup solution for codebases with versioning-like features

Author: Claude
Version: 1.0
License: MIT
"""

# Standard library imports
import argparse
import os
import sys
import json
import tarfile
import hashlib
import fnmatch
import shutil
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path


class BackupError(Exception):
    """Custom exception for backup operations"""
    pass


class FileFilter:
    """Handles file filtering with include/exclude patterns"""
    
    def __init__(self, include_patterns=None, exclude_patterns=None):
        """
        Initialize file filter with patterns
        
        Args:
            include_patterns (list): Patterns to include (default: ["*"])
            exclude_patterns (list): Patterns to exclude (default: common excludes)
        """
        self.include_patterns = include_patterns or ["*"]
        self.exclude_patterns = exclude_patterns or [
            ".git/*", ".git/**/*",
            "node_modules/*", "node_modules/**/*",
            "__pycache__/*", "__pycache__/**/*",
            "*.pyc", "*.pyo", "*.pyd",
            ".env", ".env.*",
            "*.log", "*.tmp",
            ".DS_Store", "Thumbs.db",
            "*.swp", "*.swo",
            ".vscode/*", ".idea/*"
        ]
    
    def _matches_pattern(self, file_path, pattern):
        """Check if file path matches a specific pattern"""
        # Handle both file names and full paths
        file_name = os.path.basename(file_path)
        
        # Support wildcards (* and ?)
        if fnmatch.fnmatch(file_path, pattern) or fnmatch.fnmatch(file_name, pattern):
            return True
            
        # Handle directory patterns
        if pattern.endswith('/*') or pattern.endswith('/**/*'):
            dir_pattern = pattern.rstrip('/*')
            if file_path.startswith(dir_pattern + os.sep) or file_path.startswith(dir_pattern + '/'):
                return True
                
        return False
    
    def should_include_file(self, file_path, base_path=""):
        """
        Determine if file should be included in backup
        
        Args:
            file_path (str): Full path to file
            base_path (str): Base directory for relative path calculation
            
        Returns:
            bool: True if file should be included
        """
        # Calculate relative path from base_path
        if base_path:
            try:
                relative_path = os.path.relpath(file_path, base_path)
            except ValueError:
                relative_path = file_path
        else:
            relative_path = file_path
            
        # Normalize path separators
        relative_path = relative_path.replace(os.sep, '/')
        
        # Check exclude patterns first (exclusion takes priority)
        for pattern in self.exclude_patterns:
            if self._matches_pattern(relative_path, pattern):
                return False
        
        # Check include patterns
        for pattern in self.include_patterns:
            if self._matches_pattern(relative_path, pattern):
                return True
        
        return False
    
    def get_filtered_file_list(self, source_path):
        """
        Get list of all files to backup after applying filters
        
        Args:
            source_path (str): Source directory to scan
            
        Returns:
            list: List of file paths to include in backup
        """
        file_list = []
        source_path = os.path.abspath(source_path)
        
        # Walk directory tree using os.walk()
        for root, dirs, files in os.walk(source_path):
            # Filter directories early to avoid scanning excluded dirs
            dirs[:] = [d for d in dirs if self.should_include_file(
                os.path.join(root, d), source_path
            )]
            
            # Apply file filters
            for file in files:
                file_path = os.path.join(root, file)
                # Skip special Windows devices
                if os.path.basename(file_path).lower() in ['nul', 'con', 'prn', 'aux']:
                    continue
                if self.should_include_file(file_path, source_path):
                    # Handle symlinks appropriately - include them but don't follow
                    try:
                        # Verify file is accessible
                        os.stat(file_path)
                        file_list.append(file_path)
                    except (OSError, PermissionError):
                        # Skip inaccessible files
                        continue
        
        # Return sorted list for consistency
        return sorted(file_list)
    
    def calculate_checksums(self, file_list):
        """
        Calculate SHA256 checksums for files
        
        Args:
            file_list (list): List of file paths
            
        Returns:
            dict: {file_path: checksum} mapping
        """
        checksums = {}
        
        # Iterate through file list
        for file_path in file_list:
            try:
                # Read files in chunks (4KB) for memory efficiency
                with open(file_path, 'rb') as f:
                    file_hash = hashlib.sha256()
                    for chunk in iter(lambda: f.read(4096), b""):
                        file_hash.update(chunk)
                    checksums[file_path] = file_hash.hexdigest()
            except Exception as e:
                # Handle read errors gracefully
                print(f"Warning: Could not checksum {file_path}: {e}")
                checksums[file_path] = None
        
        return checksums
    
    def get_directory_stats(self, file_list):
        """
        Calculate statistics for the file list
        
        Returns:
            dict: Statistics including file count, total size, file types
        """
        stats = {
            'file_count': len(file_list),
            'total_size': 0,
            'file_types': {},
            'largest_files': []
        }
        
        file_sizes = []
        
        for file_path in file_list:
            try:
                size = os.path.getsize(file_path)
                stats['total_size'] += size
                file_sizes.append((file_path, size))
                
                # Count files by extension
                ext = Path(file_path).suffix.lower()
                if not ext:
                    ext = 'no_extension'
                stats['file_types'][ext] = stats['file_types'].get(ext, 0) + 1
                
            except Exception:
                pass
        
        # Identify largest files (top 5)
        file_sizes.sort(key=lambda x: x[1], reverse=True)
        stats['largest_files'] = file_sizes[:5]
        
        return stats


class ArchiveManager:
    """Manages archive creation and extraction using tarfile"""
    
    def __init__(self, compression_level=6):
        """
        Initialize archive manager
        
        Args:
            compression_level (int): Compression level 0-9 (6 is good balance)
        """
        self.compression_level = compression_level
    
    def create_archive(self, file_list, source_path, archive_path):
        """
        Create compressed tar.gz archive
        
        Args:
            file_list (list): List of files to archive
            source_path (str): Base directory for relative paths
            archive_path (str): Output archive path
            
        Returns:
            int: Size of created archive in bytes
            
        Raises:
            BackupError: If archive creation fails
        """
        try:
            # Validate inputs
            if not file_list:
                raise BackupError("No files to archive")
            if not os.path.exists(source_path):
                raise BackupError(f"Source path does not exist: {source_path}")
            
            # Create parent directories if needed
            os.makedirs(os.path.dirname(archive_path), exist_ok=True)
            
            # Open tarfile with gzip compression
            with tarfile.open(archive_path, 'w:gz') as tar:
                for file_path in file_list:
                    try:
                        # Add files with relative paths (arcname)
                        arcname = os.path.relpath(file_path, source_path)
                        tar.add(file_path, arcname=arcname)
                    except Exception as e:
                        # Handle file access errors
                        print(f"Warning: Could not add {file_path} to archive: {e}")
            
            # Validate created archive
            if not self.validate_archive(archive_path):
                raise BackupError("Created archive failed validation")
            
            # Return archive size
            return os.path.getsize(archive_path)
            
        except Exception as e:
            raise BackupError(f"Failed to create archive: {e}")
    
    def extract_archive(self, archive_path, destination_path, file_list=None):
        """
        Extract archive to destination
        
        Args:
            archive_path (str): Path to archive file
            destination_path (str): Where to extract
            file_list (list, optional): Specific files to extract
            
        Returns:
            bool: Success status
            
        Raises:
            BackupError: If extraction fails
        """
        try:
            # Validate archive exists and is readable
            if not os.path.exists(archive_path):
                raise BackupError(f"Archive does not exist: {archive_path}")
            
            # Create destination directory
            os.makedirs(destination_path, exist_ok=True)
            
            # Open tarfile for reading
            with tarfile.open(archive_path, 'r:gz') as tar:
                if file_list:
                    # Extract specific files
                    for file_name in file_list:
                        try:
                            tar.extract(file_name, destination_path)
                        except KeyError:
                            print(f"Warning: File {file_name} not found in archive")
                else:
                    # Extract all files
                    tar.extractall(destination_path)
            
            return True
            
        except Exception as e:
            raise BackupError(f"Failed to extract archive: {e}")
    
    def validate_archive(self, archive_path):
        """
        Validate archive integrity without extracting
        
        Args:
            archive_path (str): Path to archive
            
        Returns:
            bool: True if archive is valid
        """
        try:
            # Try to open archive
            with tarfile.open(archive_path, 'r:gz') as tar:
                # Read member list and verify each member can be read
                for member in tar.getmembers():
                    if member.isfile():
                        # Try to read first byte of each file
                        f = tar.extractfile(member)
                        if f:
                            f.read(1)
                            f.close()
            return True
        except Exception:
            return False
    
    def list_archive_contents(self, archive_path):
        """
        List contents of archive without extracting
        
        Returns:
            list: List of file information dicts
        """
        try:
            contents = []
            with tarfile.open(archive_path, 'r:gz') as tar:
                for member in tar.getmembers():
                    if member.isfile():
                        contents.append({
                            'name': member.name,
                            'size': member.size,
                            'mtime': datetime.fromtimestamp(member.mtime),
                            'mode': oct(member.mode)
                        })
            return contents
        except Exception:
            return []


class MetadataManager:
    """Manages backup metadata in JSON format"""
    
    def __init__(self, metadata_filename='backups_metadata.json'):
        """
        Initialize metadata manager
        
        Args:
            metadata_filename (str): Name of metadata file
        """
        self.metadata_filename = metadata_filename
    
    def load_metadata(self, destination_path):
        """
        Load existing metadata or create new structure
        
        Args:
            destination_path (str): Directory containing metadata
            
        Returns:
            dict: Metadata structure
        """
        metadata_path = os.path.join(destination_path, self.metadata_filename)
        
        # Check if metadata file exists
        if os.path.exists(metadata_path):
            try:
                # Load and parse JSON if exists
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Warning: Could not load metadata: {e}")
        
        # Return default structure if not exists or invalid
        return self.get_default_metadata_structure()
    
    def save_metadata(self, destination_path, metadata):
        """
        Save metadata to JSON file
        
        Args:
            destination_path (str): Directory to save metadata
            metadata (dict): Metadata to save
            
        Raises:
            BackupError: If save fails
        """
        try:
            # Update last_modified timestamp
            metadata["last_modified"] = datetime.now(timezone.utc).isoformat()
            
            # Create destination directory if needed
            os.makedirs(destination_path, exist_ok=True)
            
            metadata_path = os.path.join(destination_path, self.metadata_filename)
            
            # Write JSON with proper formatting
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            raise BackupError(f"Failed to save metadata: {e}")
    
    def add_backup_entry(self, destination_path, backup_info):
        """
        Add new backup entry to metadata
        
        Args:
            destination_path (str): Backup destination directory
            backup_info (dict): Backup information
            
        Returns:
            dict: Updated metadata
        """
        # Load existing metadata
        metadata = self.load_metadata(destination_path)
        
        # Append new backup info
        metadata["backups"].append(backup_info)
        
        # Save updated metadata
        self.save_metadata(destination_path, metadata)
        
        return metadata
    
    def get_backup_info(self, destination_path, backup_id):
        """
        Retrieve specific backup information
        
        Args:
            destination_path (str): Backup directory
            backup_id (str): Backup identifier
            
        Returns:
            dict or None: Backup information if found
        """
        # Load metadata
        metadata = self.load_metadata(destination_path)
        
        # Search for backup by ID
        for backup in metadata["backups"]:
            if backup["id"] == backup_id:
                return backup
        return None
    
    def remove_backup_entry(self, destination_path, backup_id):
        """
        Remove backup entry from metadata
        
        Args:
            destination_path (str): Backup directory
            backup_id (str): Backup to remove
            
        Returns:
            bool: True if backup was found and removed
        """
        # Load metadata
        metadata = self.load_metadata(destination_path)
        
        # Filter out specified backup
        original_count = len(metadata["backups"])
        metadata["backups"] = [b for b in metadata["backups"] if b["id"] != backup_id]
        
        if len(metadata["backups"]) < original_count:
            # Save updated metadata
            self.save_metadata(destination_path, metadata)
            return True
        return False
    
    def get_default_metadata_structure(self):
        """
        Return default metadata structure
        
        Returns:
            dict: Default metadata structure
        """
        return {
            "backups": [],
            "config": {
                "default_includes": ["*"],
                "default_excludes": [
                    ".git/*", "node_modules/*", "__pycache__/*",
                    "*.pyc", "*.pyo", ".env", "*.log"
                ]
            },
            "created": datetime.now(timezone.utc).isoformat(),
            "last_modified": datetime.now(timezone.utc).isoformat(),
            "version": "1.0"
        }


class ConfigManager:
    """Manages persistent configuration settings"""
    
    def __init__(self, config_filename='.pybackup_config.json'):
        """
        Initialize configuration manager
        
        Args:
            config_filename (str): Configuration file name
        """
        self.config_filename = config_filename
        self.config_path = os.path.join(os.path.expanduser('~'), config_filename)
        self.config = self.load_config()
    
    def load_config(self):
        """
        Load configuration from file or create defaults
        
        Returns:
            dict: Configuration dictionary
        """
        # Check if config file exists in home directory
        if os.path.exists(self.config_path):
            try:
                # Load and parse JSON if exists
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    
                # Validate config structure - merge with defaults
                default_config = self.get_default_config()
                for key, value in default_config.items():
                    if key not in config:
                        config[key] = value
                        
                return config
            except Exception as e:
                print(f"Warning: Could not load config: {e}")
        
        # Return default config if not exists
        return self.get_default_config()
    
    def save_config(self):
        """Save current configuration to file"""
        try:
            # Write config to JSON file in home directory
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Warning: Could not save config: {e}")
    
    def get(self, key, default=None):
        """Get configuration value with default"""
        return self.config.get(key, default)
    
    def set(self, key, value):
        """Set configuration value and save"""
        self.config[key] = value
        self.save_config()
    
    def get_default_config(self):
        """
        Return default configuration structure
        
        Returns:
            dict: Default configuration
        """
        return {
            "default_destination": "",
            "max_backups": 10,
            "compression_level": 6,
            "default_includes": ["*"],
            "default_excludes": [
                ".git/*", "node_modules/*", "__pycache__/*",
                "*.pyc", "*.pyo", ".env", "*.log", "*.tmp",
                ".DS_Store", "Thumbs.db"
            ],
            "auto_cleanup": True,
            "verify_backups": True
        }


class FilenameFormatter:
    """Handles dynamic filename formatting with variables"""
    
    def __init__(self):
        self.variable_handlers = {
            # Date/Time formats
            'DDMMYY': lambda: datetime.now().strftime('%d%m%y'),
            'YYMMDD': lambda: datetime.now().strftime('%y%m%d'),
            'YYYY-MM-DD': lambda: datetime.now().strftime('%Y-%m-%d'),
            'HH-MM-SS': lambda: datetime.now().strftime('%H-%M-%S'),
            'timestamp': lambda: str(int(datetime.now().timestamp())),
            
            # Project info
            'project_name': lambda: os.path.basename(os.getcwd()),
            'branch': self._get_git_branch,
        }
    
    def format_filename(self, format_string, **kwargs):
        """
        Format filename with variable substitution
        
        Args:
            format_string (str): Format string with {variables}
            **kwargs: Additional variables (counter, project_name, etc.)
            
        Returns:
            str: Formatted filename
        """
        result = format_string
        
        # Find all variables in format string
        variables = re.findall(r'\{([^}]+)\}', format_string)
        
        for var in variables:
            value = None
            
            # Handle special cases first
            if var in ['SrNo', 'counter']:
                value = str(kwargs.get('counter', 1))
            elif var in kwargs:
                value = str(kwargs[var])
            elif var in self.variable_handlers:
                handler = self.variable_handlers[var]
                if callable(handler):
                    value = handler()
                else:
                    value = str(handler)
            
            if value is not None:
                result = result.replace(f'{{{var}}}', value)
        
        # Sanitize for filesystem compatibility
        result = self._sanitize_filename(result)
        
        return result
    
    def _get_git_branch(self):
        """Get current git branch name"""
        try:
            result = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], 
                                  capture_output=True, text=True, timeout=5)
            return result.stdout.strip() if result.returncode == 0 else 'main'
        except:
            return 'main'
    
    def _sanitize_filename(self, filename):
        """Sanitize filename for filesystem compatibility"""
        # Replace invalid characters
        invalid_chars = r'[<>:"/\\|?*]'
        filename = re.sub(invalid_chars, '_', filename)
        
        # Remove leading/trailing spaces and dots
        filename = filename.strip(' .')
        
        # Ensure filename is not too long (255 chars max for most filesystems)
        if len(filename) > 200:
            name, ext = os.path.splitext(filename)
            filename = name[:200-len(ext)] + ext
        
        return filename


class BackupConfigFile:
    """Manages project-specific backup configuration files"""
    
    def __init__(self, config_file_name='.pybackup.json'):
        """
        Initialize backup config file manager
        
        Args:
            config_file_name (str): Name of config file to look for
        """
        self.config_file_name = config_file_name
        self.supported_formats = ['.pybackup.json', '.pybackup']
    
    def find_config_file(self, search_path=None):
        """
        Find backup config file in current directory or specified path
        
        Args:
            search_path (str, optional): Directory to search in
            
        Returns:
            str or None: Path to config file if found
        """
        start_path = search_path or os.getcwd()
        current_path = os.path.abspath(start_path)
        
        # Walk up directory tree to find config file
        while True:
            # Check for .pybackup.json
            config_path = os.path.join(current_path, '.pybackup.json')
            if os.path.exists(config_path):
                return config_path
                
            # Check for .pybackup file as fallback
            config_path = os.path.join(current_path, '.pybackup')
            if os.path.exists(config_path):
                return config_path
            
            # Stop at filesystem root or git repository root
            parent = os.path.dirname(current_path)
            if parent == current_path:  # Reached filesystem root
                break
            if os.path.exists(os.path.join(current_path, '.git')):  # Git repo root
                break
                
            current_path = parent
        
        return None
    
    def load_config_file(self, config_path):
        """
        Load and parse backup configuration file
        
        Args:
            config_path (str): Path to configuration file
            
        Returns:
            dict: Parsed configuration
            
        Raises:
            BackupError: If config file is invalid
        """
        try:
            # Read config file
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Validate configuration structure
            is_valid, errors = self.validate_config(config)
            if not is_valid:
                raise BackupError(f"Invalid configuration: {', '.join(errors)}")
            
            # Apply default values for missing fields
            default_config = self.get_default_config_structure()
            for key, value in default_config.items():
                if key not in config:
                    config[key] = value
            
            # Resolve relative paths to absolute
            if config.get('destination') and not os.path.isabs(config['destination']):
                config_dir = os.path.dirname(config_path)
                config['destination'] = os.path.abspath(
                    os.path.join(config_dir, config['destination'])
                )
            
            return config
            
        except json.JSONDecodeError as e:
            raise BackupError(f"Invalid JSON in config file: {e}")
        except Exception as e:
            raise BackupError(f"Failed to load config file: {e}")
    
    def create_sample_config(self, output_path='.pybackup.json'):
        """
        Create a sample configuration file with common settings
        
        Args:
            output_path (str): Where to create the config file
            
        Returns:
            bool: Success status
        """
        try:
            sample_config = self.get_default_config_structure()
            
            # Write to specified path
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(sample_config, f, indent=4, ensure_ascii=False)
            
            return True
        except Exception:
            return False
    
    def validate_config(self, config):
        """
        Validate configuration structure and values
        
        Args:
            config (dict): Configuration to validate
            
        Returns:
            tuple: (is_valid, error_messages)
        """
        errors = []
        
        # Check required fields
        required_fields = ['backup_name', 'source_paths', 'destination']
        for field in required_fields:
            if field not in config:
                errors.append(f"Missing required field: {field}")
        
        # Validate file patterns
        if 'include_patterns' in config and not isinstance(config['include_patterns'], list):
            errors.append("include_patterns must be a list")
        if 'exclude_patterns' in config and not isinstance(config['exclude_patterns'], list):
            errors.append("exclude_patterns must be a list")
        
        # Validate filename format strings
        if 'filename_format' in config:
            try:
                formatter = FilenameFormatter()
                formatter.format_filename(config['filename_format'], counter=1)
            except Exception:
                errors.append("Invalid filename_format")
        
        return len(errors) == 0, errors
    
    def get_default_config_structure(self):
        """
        Return default configuration structure
        
        Returns:
            dict: Default backup configuration
        """
        return {
            "backup_name": "Auto Backup",
            "source_paths": ["."],
            "destination": "./backups",
            "filename_format": "{DDMMYY}_Backup_{SrNo}",
            "archive_format": "tar.gz",
            "include_patterns": ["*"],
            "exclude_patterns": [
                ".git/*", ".git/**/*",
                "node_modules/*", "node_modules/**/*",
                "__pycache__/*", "__pycache__/**/*",
                "*.pyc", "*.pyo", "*.pyd",
                ".env", ".env.*",
                "*.log", "*.tmp",
                ".DS_Store", "Thumbs.db",
                "backups/*",
                ".pybackup.json"
            ],
            "compression_level": 6,
            "generate_checksums": True,
            "auto_cleanup": {
                "enabled": True,
                "max_backups": 10,
                "max_age_days": 30
            },
            "summary": {
                "auto_generate": False,
                "default_summary": "Automated backup of {project_name}"
            }
        }


class BackupManager:
    """Main coordinator for backup operations"""
    
    def __init__(self, config_manager=None):
        """
        Initialize backup manager with all components
        
        Args:
            config_manager (ConfigManager, optional): Configuration manager instance
        """
        self.config = config_manager or ConfigManager()
        self.metadata_manager = MetadataManager()
        self.archive_manager = ArchiveManager(
            compression_level=self.config.get('compression_level', 6)
        )
        self.file_filter = FileFilter(
            include_patterns=self.config.get('default_includes'),
            exclude_patterns=self.config.get('default_excludes')
        )
        self.filename_formatter = FilenameFormatter()
    
    def create_backup(self, source_path, destination_path, name=None, summary=None, 
                     include_patterns=None, exclude_patterns=None):
        """
        Create a complete backup
        
        Args:
            source_path (str): Source directory to backup
            destination_path (str): Where to store backup
            name (str, optional): Human-readable backup name
            summary (str, optional): Backup description/summary
            include_patterns (list, optional): Custom include patterns
            exclude_patterns (list, optional): Custom exclude patterns
            
        Returns:
            str: Backup ID of created backup
            
        Raises:
            BackupError: If backup creation fails
        """
        try:
            # Validate source path exists
            if not os.path.exists(source_path):
                raise BackupError(f"Source path does not exist: {source_path}")
            
            # Create destination directory if needed
            os.makedirs(destination_path, exist_ok=True)
            
            # Generate unique backup ID with timestamp
            backup_id = self._generate_backup_id()
            
            # Set up file filter with patterns
            file_filter = FileFilter(
                include_patterns=include_patterns or self.config.get('default_includes'),
                exclude_patterns=exclude_patterns or self.config.get('default_excludes')
            )
            
            # Get filtered file list
            print("Scanning files...")
            file_list = file_filter.get_filtered_file_list(source_path)
            
            if not file_list:
                raise BackupError("No files found to backup")
            
            # Calculate file statistics and checksums
            print("Calculating checksums...")
            checksums = file_filter.calculate_checksums(file_list)
            stats = file_filter.get_directory_stats(file_list)
            
            # Create archive
            archive_name = f"{backup_id}.tar.gz"
            archive_path = os.path.join(destination_path, archive_name)
            
            print(f"Creating archive: {archive_name}")
            compressed_size = self.archive_manager.create_archive(
                file_list, source_path, archive_path
            )
            
            # Prepare backup metadata
            backup_info = {
                "id": backup_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source_path": os.path.abspath(source_path),
                "archive_name": archive_name,
                "name": name or backup_id,
                "file_count": stats['file_count'],
                "total_size": stats['total_size'],
                "compressed_size": compressed_size,
                "summary": summary or "",
                "checksums": {os.path.relpath(k, source_path): v for k, v in checksums.items()},
                "include_patterns": include_patterns or self.config.get('default_includes'),
                "exclude_patterns": exclude_patterns or self.config.get('default_excludes')
            }
            
            # Save metadata
            self.metadata_manager.add_backup_entry(destination_path, backup_info)
            
            # Clean up old backups if auto_cleanup enabled
            if self.config.get('auto_cleanup'):
                max_backups = self.config.get('max_backups', 10)
                self.cleanup_old_backups(destination_path, max_backups=max_backups)
            
            print(f"Backup completed: {backup_id}")
            print(f"Files: {stats['file_count']}, Size: {self._format_size(stats['total_size'])} -> {self._format_size(compressed_size)}")
            
            return backup_id
            
        except Exception as e:
            raise BackupError(f"Backup creation failed: {e}")
    
    def create_backup_from_config(self, config_file_path=None):
        """
        Create backup using configuration file settings
        
        Args:
            config_file_path (str, optional): Path to config file
            
        Returns:
            str: Backup ID of created backup
        """
        config_manager = BackupConfigFile()
        
        if config_file_path:
            config_path = config_file_path
        else:
            config_path = config_manager.find_config_file()
            if not config_path:
                raise BackupError("No configuration file found")
        
        # Find and load configuration file
        config = config_manager.load_config_file(config_path)
        
        # Get next backup counter for filename format
        counter = self._get_next_backup_counter(
            config['destination'], 
            config['filename_format']
        )
        
        # Resolve filename format with counter
        archive_name = self.filename_formatter.format_filename(
            config['filename_format'],
            counter=counter,
            project_name=config.get('project_name', os.path.basename(os.getcwd()))
        )
        
        # Ensure proper extension
        if not archive_name.endswith('.tar.gz'):
            archive_name += '.tar.gz'
        
        # Create backup with config settings
        source_paths = config.get('source_paths', ['.'])
        # For simplicity, use first source path
        source_path = source_paths[0] if source_paths else '.'
        
        # Generate summary from template
        summary = config.get('summary', {}).get('default_summary', '')
        if summary:
            summary = self.filename_formatter.format_filename(
                summary,
                project_name=os.path.basename(os.getcwd())
            )
        
        backup_id = self.create_backup(
            source_path=os.path.abspath(source_path),
            destination_path=config['destination'],
            name=config.get('backup_name', 'Auto Backup'),
            summary=summary,
            include_patterns=config.get('include_patterns'),
            exclude_patterns=config.get('exclude_patterns')
        )
        
        # Apply cleanup policies from config
        cleanup_config = config.get('auto_cleanup', {})
        if cleanup_config.get('enabled', True):
            self.cleanup_old_backups(
                config['destination'],
                max_backups=cleanup_config.get('max_backups'),
                max_age_days=cleanup_config.get('max_age_days')
            )
        
        return backup_id
    
    def _get_next_backup_counter(self, destination_path, filename_format):
        """
        Get next sequential backup number for filename format
        
        Args:
            destination_path (str): Backup destination
            filename_format (str): Filename format string
            
        Returns:
            int: Next available backup number
        """
        if '{SrNo}' not in filename_format and '{counter}' not in filename_format:
            return 1
        
        try:
            # Load existing metadata
            metadata = self.metadata_manager.load_metadata(destination_path)
            
            # Extract counter numbers from existing backups
            counters = []
            for backup in metadata.get('backups', []):
                archive_name = backup.get('archive_name', '')
                # Try to extract number from filename
                numbers = re.findall(r'\d+', archive_name)
                if numbers:
                    counters.extend([int(n) for n in numbers])
            
            # Return next available number
            return max(counters) + 1 if counters else 1
            
        except Exception:
            return 1
    
    def list_backups(self, destination_path=None, format='table'):
        """
        List all available backups
        
        Args:
            destination_path (str, optional): Backup location
            format (str): Output format ('table', 'json', 'simple')
            
        Returns:
            list: List of backup information
        """
        if not destination_path:
            destination_path = self.config.get('default_destination', './backups')
        
        metadata = self.metadata_manager.load_metadata(destination_path)
        backups = metadata.get('backups', [])
        
        # Sort by timestamp (newest first)
        backups.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        if format == 'json':
            print(json.dumps(backups, indent=2))
        elif format == 'simple':
            for backup in backups:
                print(f"{backup['id']} - {backup.get('name', 'Unnamed')} ({backup.get('timestamp', 'Unknown time')})")
        else:  # table format
            if not backups:
                print("No backups found.")
                return []
            
            print(f"\nFound {len(backups)} backup(s):")
            print("-" * 80)
            
            for backup in backups:
                print(f"ID: {backup['id']}")
                print(f"Name: {backup.get('name', 'Unnamed')}")
                print(f"Date: {backup.get('timestamp', 'Unknown')[:19].replace('T', ' ')}")
                print(f"Files: {backup.get('file_count', 0)}")
                
                total_size = backup.get('total_size', 0)
                compressed_size = backup.get('compressed_size', 0)
                print(f"Size: {self._format_size(total_size)} -> {self._format_size(compressed_size)}")
                
                if backup.get('summary'):
                    print(f"Summary: {backup['summary']}")
                
                print("-" * 80)
        
        return backups
    
    def restore_backup(self, backup_id, destination_path, target_path, 
                      files=None, verify=True):
        """
        Restore backup to specified location
        
        Args:
            backup_id (str): ID of backup to restore
            destination_path (str): Where backups are stored
            target_path (str): Where to restore files
            files (list, optional): Specific files to restore
            verify (bool): Verify checksums after restore
            
        Returns:
            bool: Success status
        """
        try:
            # Find backup in metadata
            backup_info = self.metadata_manager.get_backup_info(destination_path, backup_id)
            if not backup_info:
                raise BackupError(f"Backup not found: {backup_id}")
            
            archive_path = os.path.join(destination_path, backup_info['archive_name'])
            
            # Validate archive exists and is valid
            if not os.path.exists(archive_path):
                raise BackupError(f"Archive file not found: {archive_path}")
            
            if not self.archive_manager.validate_archive(archive_path):
                raise BackupError(f"Archive is corrupted: {archive_path}")
            
            # Extract archive to target path
            print(f"Restoring backup {backup_id} to {target_path}")
            self.archive_manager.extract_archive(archive_path, target_path, files)
            
            # Verify checksums if requested
            if verify and backup_info.get('checksums'):
                print("Verifying restored files...")
                self._verify_restored_files(target_path, backup_info['checksums'])
            
            print("Restore completed successfully")
            return True
            
        except Exception as e:
            raise BackupError(f"Restore failed: {e}")
    
    def _verify_restored_files(self, restored_path, original_checksums):
        """Verify checksums of restored files"""
        verified = 0
        failed = 0
        
        for rel_path, original_checksum in original_checksums.items():
            if original_checksum is None:
                continue
                
            file_path = os.path.join(restored_path, rel_path)
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'rb') as f:
                        file_hash = hashlib.sha256()
                        for chunk in iter(lambda: f.read(4096), b""):
                            file_hash.update(chunk)
                        
                        if file_hash.hexdigest() == original_checksum:
                            verified += 1
                        else:
                            failed += 1
                            print(f"Checksum mismatch: {rel_path}")
                except Exception as e:
                    failed += 1
                    print(f"Could not verify: {rel_path} - {e}")
            else:
                failed += 1
                print(f"Missing file: {rel_path}")
        
        print(f"Verification: {verified} OK, {failed} failed")
    
    def add_summary(self, backup_id, destination_path, summary):
        """
        Add or update summary for existing backup
        
        Args:
            backup_id (str): Backup identifier
            destination_path (str): Backup location
            summary (str): Summary text to add
            
        Returns:
            bool: Success status
        """
        try:
            # Load metadata
            metadata = self.metadata_manager.load_metadata(destination_path)
            
            # Find backup by ID
            for backup in metadata["backups"]:
                if backup["id"] == backup_id:
                    backup["summary"] = summary
                    backup["summary_updated"] = datetime.now(timezone.utc).isoformat()
                    
                    # Save updated metadata
                    self.metadata_manager.save_metadata(destination_path, metadata)
                    return True
            
            return False
            
        except Exception:
            return False
    
    def verify_backup(self, backup_id, destination_path):
        """
        Verify backup integrity
        
        Args:
            backup_id (str): Backup to verify
            destination_path (str): Backup location
            
        Returns:
            dict: Verification results
        """
        try:
            # Load backup metadata
            backup_info = self.metadata_manager.get_backup_info(destination_path, backup_id)
            if not backup_info:
                return {"valid": False, "error": "Backup not found"}
            
            archive_path = os.path.join(destination_path, backup_info['archive_name'])
            
            # Check archive file exists
            if not os.path.exists(archive_path):
                return {"valid": False, "error": "Archive file missing"}
            
            # Validate archive integrity
            if not self.archive_manager.validate_archive(archive_path):
                return {"valid": False, "error": "Archive is corrupted"}
            
            return {
                "valid": True,
                "backup_id": backup_id,
                "archive_path": archive_path,
                "file_count": backup_info.get('file_count', 0),
                "size": backup_info.get('compressed_size', 0)
            }
            
        except Exception as e:
            return {"valid": False, "error": str(e)}
    
    def cleanup_old_backups(self, destination_path, max_backups=None, max_age_days=None):
        """
        Clean up old backups based on policies
        
        Args:
            destination_path (str): Backup location
            max_backups (int, optional): Maximum number to keep
            max_age_days (int, optional): Maximum age in days
            
        Returns:
            list: List of removed backup IDs
        """
        try:
            metadata = self.metadata_manager.load_metadata(destination_path)
            backups = metadata.get('backups', [])
            
            if not backups:
                return []
            
            # Sort backups by timestamp (newest first)
            backups.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
            removed_backups = []
            
            # Apply max_backups policy
            if max_backups and len(backups) > max_backups:
                backups_to_remove = backups[max_backups:]
                for backup in backups_to_remove:
                    self._remove_backup_files(destination_path, backup)
                    removed_backups.append(backup['id'])
                
                # Keep only the newest backups
                backups = backups[:max_backups]
            
            # Apply max_age_days policy
            if max_age_days:
                cutoff_date = datetime.now(timezone.utc) - timedelta(days=max_age_days)
                cutoff_str = cutoff_date.isoformat()
                
                remaining_backups = []
                for backup in backups:
                    if backup.get('timestamp', '') < cutoff_str:
                        self._remove_backup_files(destination_path, backup)
                        removed_backups.append(backup['id'])
                    else:
                        remaining_backups.append(backup)
                
                backups = remaining_backups
            
            # Update metadata with remaining backups
            if removed_backups:
                metadata['backups'] = backups
                self.metadata_manager.save_metadata(destination_path, metadata)
                print(f"Cleaned up {len(removed_backups)} old backup(s)")
            
            return removed_backups
            
        except Exception as e:
            print(f"Cleanup failed: {e}")
            return []
    
    def _remove_backup_files(self, destination_path, backup_info):
        """Remove backup archive file"""
        try:
            archive_path = os.path.join(destination_path, backup_info['archive_name'])
            if os.path.exists(archive_path):
                os.remove(archive_path)
        except Exception as e:
            print(f"Could not remove {backup_info['archive_name']}: {e}")
    
    def get_backup_statistics(self, destination_path):
        """
        Get statistics about all backups
        
        Returns:
            dict: Statistics including total size, count, etc.
        """
        try:
            metadata = self.metadata_manager.load_metadata(destination_path)
            backups = metadata.get('backups', [])
            
            if not backups:
                return {"total_backups": 0, "total_size": 0}
            
            total_size = sum(b.get('compressed_size', 0) for b in backups)
            total_files = sum(b.get('file_count', 0) for b in backups)
            
            return {
                "total_backups": len(backups),
                "total_size": total_size,
                "total_files": total_files,
                "oldest_backup": min(b.get('timestamp', '') for b in backups),
                "newest_backup": max(b.get('timestamp', '') for b in backups)
            }
            
        except Exception:
            return {"total_backups": 0, "total_size": 0}
    
    def _generate_backup_id(self):
        """Generate unique backup identifier"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = os.urandom(3).hex()
        return f"backup_{timestamp}_{random_suffix}"
    
    def _format_size(self, size_bytes):
        """Format file size in human-readable format"""
        if size_bytes == 0:
            return "0 B"
            
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"


class CLIInterface:
    """Command line interface handler"""
    
    def __init__(self):
        """Initialize CLI interface"""
        self.config_manager = ConfigManager()
        self.backup_manager = BackupManager(self.config_manager)
    
    def create_parser(self):
        """
        Create and configure argument parser
        
        Returns:
            argparse.ArgumentParser: Configured parser
        """
        parser = argparse.ArgumentParser(
            description='PyBackup_Tool - Zero Dependencies Backup Tool',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  %(prog)s backup                                    # Use config file
  %(prog)s backup /path/to/source -d ./backups      # Traditional backup
  %(prog)s list -d ./backups                        # List backups
  %(prog)s restore backup_20250906_143022_abc123 ./restore -d ./backups
  %(prog)s init-config                              # Create sample config
            """
        )
        
        # Global options
        parser.add_argument('-v', '--verbose', action='store_true',
                          help='Enable verbose output')
        parser.add_argument('-q', '--quiet', action='store_true',
                          help='Suppress output except errors')
        
        # Create subparsers
        subparsers = parser.add_subparsers(dest='command', help='Available commands')
        
        # Backup command
        backup_parser = subparsers.add_parser('backup', help='Create a backup')
        backup_parser.add_argument('source_path', nargs='?', 
                                 help='Source directory to backup')
        backup_parser.add_argument('-d', '--destination', 
                                 help='Backup destination directory')
        backup_parser.add_argument('-n', '--name', 
                                 help='Human-readable backup name')
        backup_parser.add_argument('-s', '--summary', 
                                 help='Backup summary/description')
        backup_parser.add_argument('--config', 
                                 help='Use specific config file')
        
        # List command
        list_parser = subparsers.add_parser('list', help='List all backups')
        list_parser.add_argument('-d', '--destination', 
                               help='Backup location')
        list_parser.add_argument('--format', choices=['table', 'json', 'simple'],
                               default='table', help='Output format')
        
        # Restore command
        restore_parser = subparsers.add_parser('restore', help='Restore a backup')
        restore_parser.add_argument('backup_id', help='Backup ID to restore')
        restore_parser.add_argument('target_path', help='Where to restore files')
        restore_parser.add_argument('-d', '--destination', required=True,
                                  help='Backup location')
        restore_parser.add_argument('--no-verify', action='store_true',
                                  help='Skip checksum verification')
        
        # Add-summary command
        summary_parser = subparsers.add_parser('add-summary', 
                                             help='Add summary to existing backup')
        summary_parser.add_argument('backup_id', help='Backup ID')
        summary_parser.add_argument('summary_text', help='Summary text')
        summary_parser.add_argument('-d', '--destination', 
                                  help='Backup location')
        
        # Verify command
        verify_parser = subparsers.add_parser('verify', help='Verify backup integrity')
        verify_parser.add_argument('backup_id', help='Backup ID to verify')
        verify_parser.add_argument('-d', '--destination', required=True,
                                 help='Backup location')
        
        # Cleanup command
        cleanup_parser = subparsers.add_parser('cleanup', help='Clean up old backups')
        cleanup_parser.add_argument('-d', '--destination', required=True,
                                  help='Backup location')
        cleanup_parser.add_argument('--max-age', type=int, 
                                  help='Maximum age in days')
        cleanup_parser.add_argument('--keep-count', type=int,
                                  help='Keep last N backups')
        
        # Configure command
        config_parser = subparsers.add_parser('configure', help='Configure tool settings')
        config_parser.add_argument('--default-destination', 
                                 help='Set default backup destination')
        config_parser.add_argument('--max-backups', type=int,
                                 help='Set maximum number of backups to keep')
        
        # Init-config command
        init_parser = subparsers.add_parser('init-config', 
                                          help='Create sample configuration file')
        init_parser.add_argument('--output', default='.pybackup.json',
                               help='Output file name')
        
        return parser
    
    def handle_backup_command(self, args):
        """Handle backup command"""
        try:
            config_file_manager = BackupConfigFile()
            
            if args.config:
                # Use specified config file
                backup_id = self.backup_manager.create_backup_from_config(args.config)
            elif not args.source_path:
                # Look for config file in current directory
                config_path = config_file_manager.find_config_file()
                if config_path:
                    print(f"Using config file: {config_path}")
                    backup_id = self.backup_manager.create_backup_from_config(config_path)
                else:
                    print("No source path specified and no config file found", file=sys.stderr)
                    print("Use 'init-config' to create a sample configuration file")
                    sys.exit(1)
            else:
                # Use traditional command line arguments
                destination = args.destination or self.config_manager.get('default_destination')
                if not destination:
                    print("No destination specified. Use -d option or configure default destination")
                    sys.exit(1)
                    
                backup_id = self.backup_manager.create_backup(
                    source_path=args.source_path,
                    destination_path=destination,
                    name=args.name,
                    summary=args.summary
                )
            
            print(f"Backup created successfully: {backup_id}")
            
        except BackupError as e:
            print(f"Backup failed: {e}", file=sys.stderr)
            sys.exit(1)
        except KeyboardInterrupt:
            print("\nBackup cancelled by user", file=sys.stderr)
            sys.exit(1)
    
    def handle_list_command(self, args):
        """Handle list command"""
        try:
            destination = args.destination or self.config_manager.get('default_destination')
            if not destination:
                print("No backup destination specified")
                sys.exit(1)
            
            self.backup_manager.list_backups(destination, args.format)
            
        except Exception as e:
            print(f"List failed: {e}", file=sys.stderr)
            sys.exit(1)
    
    def handle_restore_command(self, args):
        """Handle restore command"""
        try:
            success = self.backup_manager.restore_backup(
                backup_id=args.backup_id,
                destination_path=args.destination,
                target_path=args.target_path,
                verify=not args.no_verify
            )
            
            if success:
                print(f" Restore completed: {args.backup_id}")
            else:
                print(" Restore failed")
                sys.exit(1)
                
        except BackupError as e:
            print(f" Restore failed: {e}", file=sys.stderr)
            sys.exit(1)
    
    def handle_summary_command(self, args):
        """Handle add-summary command"""
        try:
            destination = args.destination or self.config_manager.get('default_destination')
            if not destination:
                print("No backup destination specified")
                sys.exit(1)
            
            success = self.backup_manager.add_summary(
                backup_id=args.backup_id,
                destination_path=destination,
                summary=args.summary_text
            )
            
            if success:
                print(f" Summary added to backup: {args.backup_id}")
            else:
                print(f" Backup not found: {args.backup_id}")
                sys.exit(1)
                
        except Exception as e:
            print(f" Add summary failed: {e}", file=sys.stderr)
            sys.exit(1)
    
    def handle_verify_command(self, args):
        """Handle verify command"""
        try:
            result = self.backup_manager.verify_backup(
                backup_id=args.backup_id,
                destination_path=args.destination
            )
            
            if result['valid']:
                print(f" Backup is valid: {args.backup_id}")
                print(f"  Files: {result['file_count']}")
                print(f"  Size: {self.backup_manager._format_size(result['size'])}")
            else:
                print(f" Backup verification failed: {result['error']}")
                sys.exit(1)
                
        except Exception as e:
            print(f" Verify failed: {e}", file=sys.stderr)
            sys.exit(1)
    
    def handle_cleanup_command(self, args):
        """Handle cleanup command"""
        try:
            removed = self.backup_manager.cleanup_old_backups(
                destination_path=args.destination,
                max_backups=args.keep_count,
                max_age_days=args.max_age
            )
            
            if removed:
                print(f" Cleaned up {len(removed)} backup(s)")
                for backup_id in removed:
                    print(f"  - {backup_id}")
            else:
                print(" No backups to clean up")
                
        except Exception as e:
            print(f" Cleanup failed: {e}", file=sys.stderr)
            sys.exit(1)
    
    def handle_config_command(self, args):
        """Handle configure command"""
        try:
            updated = False
            
            if args.default_destination:
                self.config_manager.set('default_destination', args.default_destination)
                print(f" Default destination set to: {args.default_destination}")
                updated = True
            
            if args.max_backups:
                self.config_manager.set('max_backups', args.max_backups)
                print(f" Max backups set to: {args.max_backups}")
                updated = True
            
            if not updated:
                print("Current configuration:")
                print(f"  Default destination: {self.config_manager.get('default_destination') or 'Not set'}")
                print(f"  Max backups: {self.config_manager.get('max_backups')}")
                print(f"  Auto cleanup: {self.config_manager.get('auto_cleanup')}")
                
        except Exception as e:
            print(f" Configuration failed: {e}", file=sys.stderr)
            sys.exit(1)
    
    def handle_init_config_command(self, args):
        """Handle init-config command"""
        try:
            config_manager = BackupConfigFile()
            
            if os.path.exists(args.output):
                response = input(f"File {args.output} already exists. Overwrite? (y/N): ")
                if response.lower() != 'y':
                    print(" Configuration creation cancelled")
                    return
            
            success = config_manager.create_sample_config(args.output)
            
            if success:
                print(f"Sample configuration created: {args.output}")
                print("Edit the file to customize your backup settings.")
                print(f"Then run: python {sys.argv[0]} backup")
            else:
                print("Failed to create configuration file", file=sys.stderr)
                sys.exit(1)
                
        except Exception as e:
            print(f" Init config failed: {e}", file=sys.stderr)
            sys.exit(1)
    
    def run(self, args=None):
        """
        Run CLI interface
        
        Args:
            args (list, optional): Command line arguments
        """
        parser = self.create_parser()
        parsed_args = parser.parse_args(args)
        
        if not parsed_args.command:
            parser.print_help()
            sys.exit(1)
        
        # Route to appropriate handler
        command_handlers = {
            'backup': self.handle_backup_command,
            'list': self.handle_list_command,
            'restore': self.handle_restore_command,
            'add-summary': self.handle_summary_command,
            'verify': self.handle_verify_command,
            'cleanup': self.handle_cleanup_command,
            'configure': self.handle_config_command,
            'init-config': self.handle_init_config_command,
        }
        
        handler = command_handlers.get(parsed_args.command)
        if handler:
            handler(parsed_args)
        else:
            print(f"Unknown command: {parsed_args.command}", file=sys.stderr)
            sys.exit(1)


def main():
    """Main entry point"""
    try:
        cli = CLIInterface()
        cli.run()
    except KeyboardInterrupt:
        print("\n Operation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f" Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
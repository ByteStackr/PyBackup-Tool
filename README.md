# PyBackup_Tool.py - Complete User Guide

## Overview

PyBackup_Tool.py is a robust, zero-dependency backup solution for codebases with versioning-like features. It creates compressed archives with metadata tracking, supports project-specific configurations, and provides comprehensive backup management capabilities.

## Features

- **Zero Dependencies** - Uses only Python standard library
- **Cross-Platform** - Works on Windows, Linux, macOS
- **Configuration Files** - Project-specific backup settings
- **Dynamic Filenames** - Custom naming with variables
- **Integrity Verification** - SHA256 checksums and validation
- **Flexible Filtering** - Include/exclude patterns
- **Metadata Tracking** - JSON-based backup information
- **AI Summary Support** - External summary integration
- **Cleanup Policies** - Automatic old backup removal

## Quick Start

### 1. Basic Usage (Traditional CLI)

```bash
# Create a backup
python PyBackup_Tool.py backup /path/to/source -d ./backups -n "My Project" -s "Project backup"

# List all backups
python PyBackup_Tool.py list -d ./backups

# Restore a backup
python PyBackup_Tool.py restore backup_20250906_143022_abc123 ./restore -d ./backups
```

### 2. Configuration File Usage (Recommended)

```bash
# Create sample configuration
python PyBackup_Tool.py init-config

# Edit .pybackup.json to customize settings
# Then create backup using config
python PyBackup_Tool.py backup
```

## Installation

No installation required! Simply download `PyBackup_Tool.py` and run with Python 3.6+.

```bash
# Check Python version
python --version

# Make executable (Linux/macOS)
chmod +x PyBackup_Tool.py

# Run directly
python PyBackup_Tool.py --help
```

## Command Reference

### Global Options

```bash
-h, --help     Show help message
-v, --verbose  Enable verbose output
-q, --quiet    Suppress output except errors
```

### Commands

#### `backup` - Create Backup

**Using Configuration File:**
```bash
python PyBackup_Tool.py backup                    # Auto-find config file
python PyBackup_Tool.py backup --config my.json  # Use specific config
```

**Traditional CLI:**
```bash
python PyBackup_Tool.py backup SOURCE_PATH -d DESTINATION [OPTIONS]

Options:
  -d, --destination DIR    Backup destination directory
  -n, --name NAME         Human-readable backup name
  -s, --summary TEXT      Backup summary/description
```

**Examples:**
```bash
# Simple backup
python PyBackup_Tool.py backup ./myproject -d ./backups

# Backup with metadata
python PyBackup_Tool.py backup ./src -d /backup/location -n "Production Build" -s "Pre-deployment backup"

# Using config file
python PyBackup_Tool.py backup
```

#### `list` - List Backups

```bash
python PyBackup_Tool.py list -d BACKUP_LOCATION [OPTIONS]

Options:
  -d, --destination DIR    Backup location
  --format FORMAT         Output format (table, json, simple)
```

**Examples:**
```bash
# Table format (default)
python PyBackup_Tool.py list -d ./backups

# JSON format
python PyBackup_Tool.py list -d ./backups --format json

# Simple format
python PyBackup_Tool.py list -d ./backups --format simple
```

#### `restore` - Restore Backup

```bash
python PyBackup_Tool.py restore BACKUP_ID TARGET_PATH -d BACKUP_LOCATION [OPTIONS]

Options:
  -d, --destination DIR    Backup location (required)
  --no-verify             Skip checksum verification
```

**Examples:**
```bash
# Restore with verification
python PyBackup_Tool.py restore backup_20250906_143022_abc123 ./restored -d ./backups

# Restore without verification
python PyBackup_Tool.py restore backup_20250906_143022_abc123 ./restored -d ./backups --no-verify
```

#### `add-summary` - Add Summary

```bash
python PyBackup_Tool.py add-summary BACKUP_ID "SUMMARY TEXT" -d BACKUP_LOCATION
```

**Examples:**
```bash
# Add AI-generated summary
python PyBackup_Tool.py add-summary backup_20250906_143022_abc123 "Flask web app with user authentication" -d ./backups

# Update existing summary
python PyBackup_Tool.py add-summary backup_20250906_143022_abc123 "Updated: Added payment integration" -d ./backups
```

#### `verify` - Verify Backup

```bash
python PyBackup_Tool.py verify BACKUP_ID -d BACKUP_LOCATION
```

**Examples:**
```bash
# Verify backup integrity
python PyBackup_Tool.py verify backup_20250906_143022_abc123 -d ./backups
```

#### `cleanup` - Clean Old Backups

```bash
python PyBackup_Tool.py cleanup -d BACKUP_LOCATION [OPTIONS]

Options:
  -d, --destination DIR    Backup location (required)
  --max-age DAYS          Maximum age in days
  --keep-count COUNT      Keep last N backups
```

**Examples:**
```bash
# Remove backups older than 30 days
python PyBackup_Tool.py cleanup -d ./backups --max-age 30

# Keep only last 5 backups
python PyBackup_Tool.py cleanup -d ./backups --keep-count 5

# Combine both policies
python PyBackup_Tool.py cleanup -d ./backups --max-age 60 --keep-count 10
```

#### `configure` - Tool Configuration

```bash
python PyBackup_Tool.py configure [OPTIONS]

Options:
  --default-destination DIR    Set default backup destination
  --max-backups COUNT         Set maximum number of backups to keep
```

**Examples:**
```bash
# Show current configuration
python PyBackup_Tool.py configure

# Set default destination
python PyBackup_Tool.py configure --default-destination ./backups

# Set max backups
python PyBackup_Tool.py configure --max-backups 15
```

#### `init-config` - Create Configuration File

```bash
python PyBackup_Tool.py init-config [OPTIONS]

Options:
  --output FILENAME    Output file name (default: .pybackup.json)
```

**Examples:**
```bash
# Create default config file
python PyBackup_Tool.py init-config

# Create custom config file
python PyBackup_Tool.py init-config --output myproject-backup.json
```

## Configuration File Format

### Sample .pybackup.json

```json
{
    "backup_name": "MyProject Backup",
    "source_paths": ["."],
    "destination": "./backups",
    "filename_format": "{DDMMYY}_MyProject_Backup_{SrNo}",
    "archive_format": "tar.gz",
    "include_patterns": [
        "src/**/*",
        "*.py", "*.js", "*.html", "*.css",
        "package.json", "requirements.txt",
        "README.md", "LICENSE"
    ],
    "exclude_patterns": [
        ".git/*",
        "node_modules/*",
        "__pycache__/*",
        "*.pyc", "*.pyo",
        ".env*",
        "*.log", "*.tmp",
        ".vscode/*", ".idea/*",
        "backups/*",
        ".pybackup.json"
    ],
    "compression_level": 6,
    "generate_checksums": true,
    "auto_cleanup": {
        "enabled": true,
        "max_backups": 15,
        "max_age_days": 60
    },
    "summary": {
        "auto_generate": false,
        "default_summary": "Automated backup of {project_name}"
    }
}
```

### Configuration Options

#### Basic Settings
- **`backup_name`** - Human-readable name for backups
- **`source_paths`** - Array of directories to backup (relative to config file)
- **`destination`** - Where to store backup archives
- **`filename_format`** - Custom filename with variables
- **`archive_format`** - Archive type (currently only "tar.gz")
- **`compression_level`** - Gzip compression level (0-9, default: 6)

#### File Filtering
- **`include_patterns`** - Files/patterns to include (supports wildcards)
- **`exclude_patterns`** - Files/patterns to exclude (takes precedence)

#### Advanced Options
- **`generate_checksums`** - Calculate SHA256 checksums (true/false)
- **`auto_cleanup`** - Automatic cleanup policies
- **`summary`** - Default summary settings

### Filename Format Variables

Use these variables in `filename_format`:

#### Date/Time Variables
- **`{DDMMYY}`** - Day/Month/Year (e.g., "060925")
- **`{YYMMDD}`** - Year/Month/Day (e.g., "250906")
- **`{YYYY-MM-DD}`** - Full date (e.g., "2025-09-06")
- **`{HH-MM-SS}`** - Time (e.g., "14-30-22")
- **`{timestamp}`** - Unix timestamp

#### Counter Variables
- **`{SrNo}`** - Sequential number (1, 2, 3...)
- **`{counter}`** - Same as SrNo

#### Project Variables
- **`{project_name}`** - Current directory name
- **`{branch}`** - Git branch name (if available)

**Examples:**
```json
"filename_format": "{DDMMYY}_Backup_{SrNo}"                    // 060925_Backup_1.tar.gz
"filename_format": "{project_name}_{YYYY-MM-DD}_{counter}"     // MyProject_2025-09-06_1.tar.gz
"filename_format": "{branch}_{timestamp}_backup"              // main_1725610800_backup.tar.gz
```

## File Filtering Patterns

### Include Patterns
Specify what files to include in backups:

```json
"include_patterns": [
    "*",                    // Include all files (default)
    "src/**/*",            // All files in src directory and subdirectories
    "*.py",                // All Python files
    "*.{js,ts,jsx,tsx}",   // JavaScript/TypeScript files
    "docs/*.md"            // Markdown files in docs directory
]
```

### Exclude Patterns
Specify what files to exclude (takes precedence over includes):

```json
"exclude_patterns": [
    ".git/*",              // Git directory
    "node_modules/*",      // Node.js dependencies
    "__pycache__/*",       // Python cache
    "*.pyc",               // Compiled Python files
    "*.log",               // Log files
    ".env*",               // Environment files
    "tmp/*",               // Temporary directory
    "backups/*"            // Don't backup the backup directory!
]
```

### Pattern Syntax
- **`*`** - Match any characters except directory separator
- **`**`** - Match any characters including directory separators
- **`?`** - Match single character
- **`[abc]`** - Match any character in brackets
- **`{a,b,c}`** - Match any of the comma-separated patterns

## Workflows

### 1. Personal Project Backup

```bash
# Setup
cd myproject
python PyBackup_Tool.py init-config
# Edit .pybackup.json as needed

# Daily backup
python PyBackup_Tool.py backup

# View backups
python PyBackup_Tool.py list -d ./backups

# Restore if needed
python PyBackup_Tool.py restore backup_id ./recovery -d ./backups
```

### 2. Team Project with Version Control

```bash
# Setup (commit .pybackup.json to git)
python PyBackup_Tool.py init-config
git add .pybackup.json
git commit -m "Add backup configuration"

# Each team member can now:
python PyBackup_Tool.py backup  # Uses same config
```

### 3. Automated Backup Script

```bash
#!/bin/bash
# backup.sh - Automated daily backup

cd /path/to/project
python PyBackup_Tool.py backup

# Add AI summary (example with external tool)
BACKUP_ID=$(python PyBackup_Tool.py list -d ./backups --format json | jq -r '.[0].id')
SUMMARY=$(ai-summarize-tool /path/to/project)
python PyBackup_Tool.py add-summary "$BACKUP_ID" "$SUMMARY" -d ./backups

# Cleanup old backups
python PyBackup_Tool.py cleanup -d ./backups --max-age 30
```

### 4. CI/CD Integration

```yaml
# .github/workflows/backup.yml
name: Backup
on:
  push:
    branches: [main]

jobs:
  backup:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v2
    - name: Create backup
      run: |
        python PyBackup_Tool.py backup . -d /backup/storage -n "CI Build ${{ github.run_number }}"
    - name: Cleanup old backups
      run: |
        python PyBackup_Tool.py cleanup -d /backup/storage --keep-count 10
```

## Metadata and Storage

### Backup Metadata Structure

Each backup destination contains a `backups_metadata.json` file:

```json
{
    "backups": [
        {
            "id": "backup_20250906_143022_abc123",
            "timestamp": "2025-09-06T14:30:22Z",
            "source_path": "/path/to/source",
            "archive_name": "backup_20250906_143022_abc123.tar.gz",
            "name": "My Project Backup",
            "file_count": 1247,
            "total_size": 52428800,
            "compressed_size": 12582912,
            "summary": "Flask web application with user authentication",
            "summary_updated": "2025-09-06T14:35:00Z",
            "checksums": {...},
            "include_patterns": ["*"],
            "exclude_patterns": [".git/*", "node_modules/*"]
        }
    ],
    "config": {...},
    "created": "2025-09-06T14:30:22Z",
    "last_modified": "2025-09-06T14:35:00Z",
    "version": "1.0"
}
```

### Directory Structure

```
backup_destination/
├── backups_metadata.json          # Metadata for all backups
├── backup_20250906_143022_abc123.tar.gz    # Backup archive
├── backup_20250906_150500_def456.tar.gz    # Another backup
└── backup_20250906_172000_ghi789.tar.gz    # Latest backup
```

## Advanced Usage

### Custom Configuration File Location

```bash
# Use specific config file
python PyBackup_Tool.py backup --config /path/to/custom.json

# Config file search order:
# 1. --config specified file
# 2. .pybackup.json in current directory
# 3. .pybackup in current directory
# 4. Walk up directory tree looking for config files
# 5. Stop at git root or filesystem root
```

### Multiple Source Paths

```json
{
    "source_paths": [
        "src",
        "docs",
        "config",
        "../shared/common"
    ]
}
```
*Note: Currently only the first source path is used. Multiple paths support is planned for future versions.*

### Integration with External AI Tools

```bash
# Example: Generate summary with external AI tool
BACKUP_ID=$(python PyBackup_Tool.py backup | grep "Backup created" | cut -d: -f2 | tr -d ' ')
SUMMARY=$(curl -X POST "https://api.openai.com/v1/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-3.5-turbo","prompt":"Analyze this codebase and provide a 4-line summary..."}' \
  | jq -r '.choices[0].text')
python PyBackup_Tool.py add-summary "$BACKUP_ID" "$SUMMARY" -d ./backups
```

### Backup Verification Workflow

```bash
# Create backup
BACKUP_ID=$(python PyBackup_Tool.py backup | grep "successfully" | cut -d: -f2 | tr -d ' ')

# Verify immediately
python PyBackup_Tool.py verify "$BACKUP_ID" -d ./backups

# Test restore to temporary location
mkdir temp_restore
python PyBackup_Tool.py restore "$BACKUP_ID" temp_restore -d ./backups
# ... test restored files ...
rm -rf temp_restore
```

## Troubleshooting

### Common Issues

#### 1. Permission Errors
```bash
# Error: Permission denied
# Solution: Check file/directory permissions
ls -la /path/to/backup/destination
chmod 755 /path/to/backup/destination
```

#### 2. Large File Handling
```bash
# Error: Archive too large / Memory error
# Solution: Exclude large files or directories
{
    "exclude_patterns": [
        "*.mp4", "*.avi", "*.mkv",  // Video files
        "*.iso", "*.dmg",           // Disk images
        "data/large_datasets/*",    // Large data directories
        "node_modules/*"            // Dependencies
    ]
}
```

#### 3. Config File Not Found
```bash
# Error: No configuration file found
# Solution: Create config file or specify source path
python PyBackup_Tool.py init-config
# OR
python PyBackup_Tool.py backup /path/to/source -d ./backups
```

#### 4. Checksum Verification Failures
```bash
# Error: Checksum mismatch during restore
# Solution: Re-create backup or restore without verification
python PyBackup_Tool.py restore backup_id ./restore -d ./backups --no-verify
```

#### 5. Windows Path Issues
```bash
# Error: Path issues on Windows
# Solution: Use forward slashes in config files
{
    "destination": "./backups",        // Good
    "source_paths": ["./src"]          // Good
}
```

### Performance Tips

#### 1. Exclude Unnecessary Files
```json
"exclude_patterns": [
    ".git/*",           // Version control
    "node_modules/*",   // Dependencies (can be reinstalled)
    "*.log",           // Log files
    "tmp/*",           // Temporary files
    "__pycache__/*",   // Compiled files
    "*.pyc",
    ".env*",           // Environment files (contain secrets)
    "coverage/*",      // Test coverage reports
    "dist/*",          // Build artifacts
    ".tox/*"           // Testing environments
]
```

#### 2. Optimize Compression
```json
{
    "compression_level": 1    // Faster, larger files
    // OR
    "compression_level": 9    // Slower, smaller files
    // Default: 6 (good balance)
}
```

#### 3. Skip Checksums for Large Backups
```json
{
    "generate_checksums": false    // Faster backup, no verification
}
```

### Debugging

#### Enable Verbose Output
```bash
python PyBackup_Tool.py backup -v    # Verbose mode
python PyBackup_Tool.py list -d ./backups -v
```

#### Check Backup Contents Without Restoring
```bash
# List archive contents (requires manual implementation)
tar -tzf ./backups/backup_20250906_143022_abc123.tar.gz | head -20
```

#### Validate Configuration File
```bash
# Check JSON syntax
python -m json.tool .pybackup.json > /dev/null && echo "Valid JSON" || echo "Invalid JSON"

# Test configuration
python PyBackup_Tool.py backup --config .pybackup.json -v
```

## License

This tool is provided as-is under the MIT License. You're free to modify and distribute it according to your needs.

---

**PyBackup_Tool.py** - Your reliable, zero-dependency backup solution! 🚀
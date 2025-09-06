from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="PyBackup-Tool",
    version="1.0.0",
    author="ByteStackr",
    author_email="229177070+ByteStackr@users.noreply.github.com",
    description="Zero-dependency Python backup tool with configuration file support",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/ByteStackr/PyBackup-Tool",
    project_urls={
        "Bug Tracker": "https://github.com/ByteStackr/PyBackup-Tool/issues",
        "Documentation": "https://github.com/ByteStackr/PyBackup-Tool#readme",
        "Source Code": "https://github.com/ByteStackr/PyBackup-Tool",
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: System :: Archiving :: Backup",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Utilities",
    ],
    packages=find_packages(),
    py_modules=["PyBackup_Tool"],
    python_requires=">=3.6",
    install_requires=[],
    entry_points={
        "console_scripts": [
            "pybackup-tool=PyBackup_Tool:main",
            "PyBackup-Tool=PyBackup_Tool:main",
        ],
    },
    keywords="backup, cli, tool, zero-dependency, configuration, versioning",
    include_package_data=True,
    zip_safe=False,
)

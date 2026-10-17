"""Stage the read-only Rust workspace with independent maturin wheel metadata.

Without this isolated pyproject, maturin inherits the application's hatch project
name and creates a wheel that replaces aurora_binaural instead of aurora_core.
"""

import shutil
import sys
import tomllib
from pathlib import Path

source, destination = map(Path, sys.argv[1:])
shutil.copytree(source / "core", destination / "core", ignore=shutil.ignore_patterns("target", "__pycache__"))
package = tomllib.loads((source / "core/aurora-py/Cargo.toml").read_text())["package"]
(destination / "core/aurora-py/pyproject.toml").write_text(
    '[build-system]\nrequires = ["maturin>=1.9,<2"]\nbuild-backend = "maturin"\n'
    f'[project]\nname = "aurora_core"\nversion = "{package["version"]}"\n'
    'requires-python = ">=3.12"\ndependencies = ["numpy>=2"]\n'
    '[tool.maturin]\nmodule-name = "aurora_core"\n',
    encoding="utf-8",
)

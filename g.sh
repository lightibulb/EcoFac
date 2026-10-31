#!/usr/bin/env bash
set -euo pipefail

COPYRIGHT_TEXT="# Copyright 2026 Lightibulb"

echo "==> 1. Updating pyproject.toml to enforce copyright..."
# We use sed to remove the CPY ignore line, then append the specific notice config
sed -i.bak '/"CPY",/d' pyproject.toml
rm -f pyproject.toml.bak

cat >> pyproject.toml <<TOML

[tool.ruff.lint.flake8-copyright]
notice = "$COPYRIGHT_TEXT"
TOML

echo "==> 2. Injecting copyright headers into all Python files..."
# Find all .py files in src and tests
find src tests -name "*.py" | while read -r file; do
    # Check if the file already has the copyright to prevent duplicates
    if ! grep -qF "$COPYRIGHT_TEXT" "$file"; then
        echo "Adding header to $file"
        # Create a temporary file, write the header, append the original content, then replace
        echo "$COPYRIGHT_TEXT" > "${file}.tmp"
        cat "$file" >> "${file}.tmp"
        mv "${file}.tmp" "$file"
    fi
done

echo "==> Done. Copyright headers added and configured."

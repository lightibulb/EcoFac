#!/usr/bin/env bash
set -euo pipefail

echo "==> 1. Fixing pyproject.toml copyright configuration..."
# Remove the old, invalid [tool.ruff.lint.flake8-copyright] block
# This creates a backup file just in case
cp pyproject.toml pyproject.toml.bak
sed -i '/\[tool.ruff.lint.flake8-copyright\]/,$d' pyproject.toml

# Append the correct block with notice-rgx
# We use \\d{4} to match any year, making it robust for future updates
cat >> pyproject.toml <<'TOML'
[tool.ruff.lint.flake8-copyright]
notice-rgx = "^# Copyright \\d{4} Lightibulb"
TOML

echo "==> 2. Verifying copyright headers in all Python files..."
# Ensure the header is present in all .py files (in case the previous script missed any)
COPYRIGHT_TEXT="# Copyright 2026 Lightibulb"
find src tests -name "*.py" | while read -r file; do
    if ! grep -qF "$COPYRIGHT_TEXT" "$file"; then
        echo "Adding missing header to $file"
        echo "$COPYRIGHT_TEXT" > "${file}.tmp"
        cat "$file" >> "${file}.tmp"
        mv "${file}.tmp" "$file"
    fi
done

echo "==> 3. Cleaning up backup file..."
rm -f pyproject.toml.bak

echo "==> Done. Configuration corrected."

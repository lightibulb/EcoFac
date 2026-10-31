# Copyright 2026 Lightibulb

"""Generates the OpenAPI JSON schema for CI verification."""

import json
import os
import sys

# Add the project root to sys.path so it can find 'ecofac' and 'simulator'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ecofac.main import app  # noqa: E402


def main() -> None:
    """Dump the OpenAPI schema to a file."""
    schema = app.openapi()
    with open("openapi.json", "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)
    print("OpenAPI schema generated successfully.")


if __name__ == "__main__":
    main()

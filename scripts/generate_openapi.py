# Copyright 2026 Lightibulb

"""Generates the OpenAPI JSON schema for CI verification."""

import json
import pathlib
import sys

# Add the project root to sys.path so it can find 'ecofac' and 'simulator'
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ecofac.main import app


def main() -> None:
    """Dump the OpenAPI schema to a file."""
    schema = app.openapi()
    with open("openapi.json", "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)
    print("OpenAPI schema generated successfully.")


if __name__ == "__main__":
    main()

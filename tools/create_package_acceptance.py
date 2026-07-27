from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_release_builder",
    ROOT / "tools" / "release_builder.py",
)
assert SPEC and SPEC.loader
release_builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_builder
SPEC.loader.exec_module(release_builder)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--release-verification", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.output.exists():
        raise SystemExit("package acceptance exists; refusing overwrite")
    result = release_builder.create_package_acceptance(
        arguments.manifest.resolve(),
        arguments.evidence.resolve(),
        arguments.release_verification.resolve(),
        arguments.output.resolve(),
    )
    print(
        json.dumps(
            {
                "target": result["target"],
                "package_acceptance": result["package_acceptance"],
                "output": str(arguments.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

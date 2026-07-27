from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from promotion import promote_candidate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Promote accepted OpenCode candidate bytes to stable assets "
            "without rebuilding."
        )
    )
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--final-evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    result = promote_candidate(
        arguments.candidate.resolve(),
        arguments.final_evidence.resolve(),
        arguments.output.resolve(),
    )
    print(
        json.dumps(
            {
                "status": "STABLE_ASSETS_PREPARED",
                "zip": str(result.zip_path),
                "zip_sha256": result.zip_sha256,
                "published": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

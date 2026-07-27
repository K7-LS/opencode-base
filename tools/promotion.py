from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import final_evidence
import release_builder


TARGET = "opencode"
TAG_PREFIX = "opencode-v"
REQUIRED_VERDICTS = (
    "CLIENT_BINARY_ACCEPTANCE",
    "CANDIDATE_OFFLINE",
    "PROVIDER_NEUTRAL_ACCEPTANCE",
    "OPENCODE_PROVIDER_MARKER",
    "OPENCODE_CANARY",
    "FULL_RELEASE_OPENCODE",
)


@dataclass(frozen=True)
class PromotionResult:
    zip_path: Path
    manifest_path: Path
    component_lock_path: Path
    evidence_path: Path
    zip_sha256: str


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _verify_final(
    evidence: dict[str, Any],
    binding: dict[str, object],
) -> None:
    verdicts = evidence.get("verdicts")
    if (
        evidence.get("schema_version") != 1
        or evidence.get("target") != TARGET
        or evidence.get("version") != binding.get("version")
        or evidence.get("release_binding") != binding
        or evidence.get("asset_sha256")
        != binding.get("asset", {}).get("sha256")
        or evidence.get("evidence_body_sha256")
        != final_evidence.evidence_body_sha256(evidence)
        or not isinstance(verdicts, dict)
    ):
        raise ValueError("final acceptance evidence is invalid or unbound")
    for gate in REQUIRED_VERDICTS:
        if verdicts.get(gate) != "PASS":
            raise ValueError(f"{gate} is not PASS")
    if verdicts.get("RELEASE_INTEGRITY") != "PENDING_PUBLICATION":
        raise ValueError("RELEASE_INTEGRITY is not pending publication")


def _verify_candidate(
    candidate_dir: Path,
) -> tuple[
    dict[str, Any],
    dict[str, object],
    Path,
    bytes,
    bytes,
]:
    manifest_path = candidate_dir / "release-manifest.json"
    evidence_path = candidate_dir / "candidate-acceptance.json"
    lock_path = candidate_dir / "components.lock.json"
    manifest = _load_json(manifest_path)
    evidence = _load_json(evidence_path)
    version = str(manifest.get("version") or "")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("target") != TARGET
        or manifest.get("channel") != "candidate"
        or manifest.get("tag") != f"{TAG_PREFIX}{version}"
    ):
        raise ValueError(
            "source release manifest is not an OpenCode candidate"
        )
    binding = release_builder.release_binding_from_manifest(manifest)
    if (
        evidence.get("schema_version") != 1
        or evidence.get("target") != TARGET
        or evidence.get("CANDIDATE_OFFLINE") != "PASS"
        or evidence.get("CLIENT_BINARY_ACCEPTANCE") != "PASS"
        or evidence.get("release_binding") != binding
        or evidence.get("evidence_body_sha256")
        != final_evidence.evidence_body_sha256(evidence)
        or _sha256(evidence_path.read_bytes())
        != manifest.get("acceptance_evidence_sha256")
    ):
        raise ValueError("candidate acceptance evidence is invalid")
    lock_bytes = lock_path.read_bytes()
    if _sha256(lock_bytes) != manifest.get("components_lock_sha256"):
        raise ValueError("candidate components lock hash differs")
    asset = manifest.get("asset")
    if not isinstance(asset, dict):
        raise ValueError("candidate asset record is invalid")
    zip_path = candidate_dir / str(asset.get("name") or "")
    zip_bytes = zip_path.read_bytes()
    if (
        zip_path.name != f"{TARGET}-base-{version}.zip"
        or _sha256(zip_bytes) != asset.get("sha256")
        or len(zip_bytes) != asset.get("bytes")
    ):
        raise ValueError("candidate ZIP hash differs")
    with zipfile.ZipFile(zip_path) as archive:
        package_manifest_bytes = archive.read("package-manifest.json")
        lock_names = [
            name
            for name in archive.namelist()
            if name.endswith("/base/components.lock.json")
        ]
        if len(lock_names) != 1:
            raise ValueError("candidate embedded components lock is ambiguous")
        embedded_lock = archive.read(lock_names[0])
    if (
        _sha256(package_manifest_bytes)
        != manifest.get("package_manifest_sha256")
        or embedded_lock != lock_bytes
    ):
        raise ValueError("candidate embedded package evidence differs")
    package_manifest = json.loads(package_manifest_bytes)
    if (
        not isinstance(package_manifest, dict)
        or package_manifest.get("target") != TARGET
        or package_manifest.get("version") != version
    ):
        raise ValueError("candidate package identity differs")
    return manifest, binding, zip_path, zip_bytes, lock_bytes


def promote_candidate(
    candidate_dir: Path,
    final_evidence_path: Path,
    output_dir: Path,
) -> PromotionResult:
    """Promote accepted candidate ZIP bytes without rebuilding them."""

    (
        candidate_manifest,
        binding,
        source_zip,
        zip_bytes,
        lock_bytes,
    ) = _verify_candidate(candidate_dir.resolve())
    final_bytes = final_evidence_path.resolve().read_bytes()
    final = json.loads(final_bytes)
    if not isinstance(final, dict):
        raise ValueError("final evidence must contain an object")
    _verify_final(final, binding)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError("stable output directory must be empty")
    output_dir.mkdir(parents=True, exist_ok=True)

    destination_zip = output_dir / source_zip.name
    destination_zip.write_bytes(zip_bytes)
    destination_lock = output_dir / "components.lock.json"
    destination_lock.write_bytes(lock_bytes)
    destination_evidence = output_dir / "acceptance-evidence.json"
    destination_evidence.write_bytes(final_bytes)

    stable_manifest = dict(candidate_manifest)
    stable_manifest["channel"] = "stable"
    stable_manifest["acceptance_evidence_sha256"] = _sha256(final_bytes)
    stable_manifest["promoted_from_candidate_manifest_sha256"] = _sha256(
        (candidate_dir / "release-manifest.json").read_bytes()
    )
    destination_manifest = output_dir / "release-manifest.json"
    destination_manifest.write_bytes(_json_bytes(stable_manifest))
    if destination_zip.read_bytes() != zip_bytes:
        raise AssertionError("promotion changed accepted candidate ZIP bytes")
    return PromotionResult(
        zip_path=destination_zip,
        manifest_path=destination_manifest,
        component_lock_path=destination_lock,
        evidence_path=destination_evidence,
        zip_sha256=_sha256(zip_bytes),
    )

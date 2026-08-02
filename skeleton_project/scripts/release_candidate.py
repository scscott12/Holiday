#!/usr/bin/env python3
"""Create, update, and verify the Raspberry Pi v1.0 acceptance record."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from holiday_skeleton.release_candidate import (
    ACCEPTANCE_CHECKS,
    MIN_SOAK_SECONDS,
    ReleaseCandidateError,
    SystemProbe,
    add_soak_sample,
    begin_soak,
    finalize_evidence,
    load_evidence,
    new_evidence,
    record_check,
    save_evidence,
    validate_sample,
    verify_digest,
    write_digest,
)


DEFAULT_DIRECTORY = Path("/var/lib/holiday-skeleton-deploy/acceptance")


def _evidence_path(options: argparse.Namespace) -> Path:
    if options.evidence is not None:
        return options.evidence
    if getattr(options, "candidate", None):
        return DEFAULT_DIRECTORY / f"{options.candidate}.json"
    raise ReleaseCandidateError("--evidence is required for this command")


def _probe() -> dict:
    return SystemProbe().capture()


def _status(payload: dict) -> None:
    print(
        f"Candidate {payload['candidate']} ({payload['expected_commit'][:12]}): "
        f"{payload['status']}"
    )
    for check in ACCEPTANCE_CHECKS:
        print(f"  {check.identifier:24} {payload['checks'][check.identifier]['status']}")
    soak = payload["soak"]
    print(
        f"  {'overnight_soak':24} {soak['status']} "
        f"({len(soak['samples'])} samples, {soak['required_seconds'] // 3600}h required)"
    )
    if payload.get("signoff"):
        print(
            f"  signed by {payload['signoff']['operator']} at "
            f"{payload['signoff']['signed_at']}"
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Maintain an auditable v1.0 Raspberry Pi acceptance evidence bundle."
    )
    commands = result.add_subparsers(dest="command", required=True)

    initialize = commands.add_parser("init", help="create a candidate evidence bundle")
    initialize.add_argument("--candidate", required=True, help="for example v1.0-rc1")
    initialize.add_argument("--expected-commit", required=True)
    initialize.add_argument("--soak-hours", type=int, default=MIN_SOAK_SECONDS // 3600)
    initialize.add_argument("--evidence", type=Path)

    record = commands.add_parser("record", help="record an observed manual check")
    record.add_argument("--evidence", type=Path, required=True)
    record.add_argument("--check", choices=[check.identifier for check in ACCEPTANCE_CHECKS], required=True)
    record.add_argument("--result", choices=("passed", "failed"), required=True)
    record.add_argument("--note", required=True)

    begin = commands.add_parser("begin-soak", help="capture the healthy soak baseline")
    begin.add_argument("--evidence", type=Path, required=True)

    sample = commands.add_parser("sample", help="append one healthy soak sample")
    sample.add_argument("--evidence", type=Path, required=True)

    status = commands.add_parser("status", help="show the current acceptance state")
    status.add_argument("--evidence", type=Path, required=True)

    finalize = commands.add_parser("finalize", help="close and hash a passing bundle")
    finalize.add_argument("--evidence", type=Path, required=True)
    finalize.add_argument("--operator", required=True)

    verify = commands.add_parser("verify", help="verify a finalized bundle and digest")
    verify.add_argument("--evidence", type=Path, required=True)
    return result


def main(arguments: list[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    try:
        evidence_path = _evidence_path(options)
        if options.command == "init":
            if evidence_path.exists() or evidence_path.is_symlink():
                raise ReleaseCandidateError(f"evidence already exists: {evidence_path}")
            payload = new_evidence(
                options.candidate,
                options.expected_commit,
                soak_seconds=options.soak_hours * 3600,
            )
            sample = _probe()
            failures = validate_sample(
                sample,
                payload["candidate"],
                payload["expected_commit"],
                require_persistent_state=False,
            )
            if failures:
                raise ReleaseCandidateError("candidate is not healthy: " + "; ".join(failures))
            save_evidence(evidence_path, payload)
            print(f"Acceptance evidence initialized: {evidence_path}")
        elif options.command == "record":
            payload = load_evidence(evidence_path)
            record_check(payload, options.check, options.result, options.note)
            save_evidence(evidence_path, payload)
            print(f"Recorded {options.check}: {options.result}")
        elif options.command == "begin-soak":
            payload = load_evidence(evidence_path)
            begin_soak(payload, _probe())
            save_evidence(evidence_path, payload)
            print("Overnight soak started with a healthy baseline")
        elif options.command == "sample":
            payload = load_evidence(evidence_path)
            add_soak_sample(payload, _probe())
            save_evidence(evidence_path, payload)
            print(f"Healthy soak sample recorded ({len(payload['soak']['samples'])} total)")
        elif options.command == "status":
            _status(load_evidence(evidence_path))
        elif options.command == "finalize":
            payload = load_evidence(evidence_path)
            if payload.get("status") != "passed":
                finalize_evidence(payload, _probe(), options.operator)
                save_evidence(evidence_path, payload)
            digest_path = write_digest(evidence_path)
            print(f"Release candidate passed: {evidence_path}")
            print(f"Evidence digest: {digest_path}")
        elif options.command == "verify":
            digest = verify_digest(evidence_path)
            print(f"Final evidence verified: sha256 {digest}")
        else:
            raise ReleaseCandidateError("unknown command")
        return 0
    except ReleaseCandidateError as error:
        print(f"Release acceptance failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

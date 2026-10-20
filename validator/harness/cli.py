# SPDX-License-Identifier: MIT
"""Author-side CLI for the offline fixture harness: verify a binding, print the plan, and refuse execution.

    python -m validator.harness verify --root DIR --binding URI
    python -m validator.harness plan   --root DIR --binding URI --runtime DIR --work DIR --input DIR
    python -m validator.harness run    # always refuses: no execution authorization exists

``verify`` and ``plan`` are static: they read files through the descriptor-bound resolver, check both digest links and the
receipt, and print a plan. ``run`` has no implementation until the Ingenjör's execution boundary is approved.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import canonical, manifest, plan, resolver

DEFAULT_LIMIT = 8 * 1024 * 1024


def _load(root: str, binding_uri: str, limit: int, schema: dict | None = None):
    binding = resolver.resolve_json(root, binding_uri, limit)
    uri, expected = binding.get("manifest_uri"), binding.get("manifest_sha256")
    if not isinstance(uri, str) or not isinstance(expected, str):
        raise manifest.ManifestError("the binding file needs a relative manifest_uri and a manifest_sha256")
    raw = resolver.resolve(root, uri, limit)
    document = canonical.loads_strict(raw)
    declared_binding = binding.get("binding_sha256")
    findings = manifest.accept(
        document, raw,
        expected_manifest_sha256=expected,
        expected_binding_sha256=declared_binding if isinstance(declared_binding, str) else None,
        schema=schema,
    )
    return document, findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m validator.harness")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "plan"):
        command = sub.add_parser(name)
        command.add_argument("--root", required=True, help="a trusted root; every URI is resolved below it")
        command.add_argument("--binding", required=True, help="the binding file's relative URI")
        command.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
        command.add_argument("--schema", default=None, help="a trusted path to the frozen manifest JSON Schema")
    sub.add_parser("run", help="refused: fixture execution is not authorized")
    sub.choices["plan"].add_argument("--runtime", required=True, help="the pinned minimal runtime image")
    sub.choices["plan"].add_argument("--work", required=True, help="a private scratch directory")
    sub.choices["plan"].add_argument("--input", required=True, help="a frozen read-only input snapshot")
    args = parser.parse_args(argv)

    if args.command == "run":
        print("execution is not authorized: the execution boundary is not approved", file=sys.stderr)
        return 2
    schema = None
    if args.schema is not None:
        try:
            schema = canonical.loads_strict(pathlib.Path(args.schema).read_bytes())
        except (OSError, canonical.CanonicalError) as exc:
            print(f"INVALID: the schema cannot be read: {exc}", file=sys.stderr)
            return 2
    try:
        document, findings = _load(args.root, args.binding, args.limit, schema)
    except (resolver.ResolverError, canonical.CanonicalError, manifest.ManifestError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    if findings:
        for finding in findings:
            print(finding, file=sys.stderr)
        return 1
    if args.command == "verify":
        if schema is None:
            print("VALID: manifest semantic subset accepted (schema validation is the caller's; "
                  "input and artifact bytes are verified by the author gate, not here)")
        else:
            print("VALID: manifest, projection, receipt and schema accepted "
                  "(input and artifact bytes are verified by the author gate, not here)")
        return 0
    try:
        runtime = plan.runtime_plan(args.runtime)
        full = plan.plan_command(runtime, work=args.work, input_dir=args.input,
                                 argv=document["bindings"]["argv"], env=document["config"]["env"])
    except (KeyError, TypeError, plan.PlanError) as exc:
        print(f"INVALID: the manifest cannot be planned: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(full, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

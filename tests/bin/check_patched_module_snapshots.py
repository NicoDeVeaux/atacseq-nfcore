#!/usr/bin/env python3
"""Check that patched nf-core modules agree with their checked-in test snapshots.

nf-test.config ignores 'modules/nf-core/**/tests/*' -- upstream modules are tested
in nf-core/modules, so running them here would duplicate that at significant CI
cost. The exclusion is right for unmodified modules, but it also means a *patched*
module is never verified locally: `nf-core modules patch` can change the tool
version while the vendored snapshot keeps asserting the upstream one, and nothing
notices.

This checks only modules carrying a `patch` entry in modules.json, comparing the
version declared in environment.yml against the versions recorded in the module's
test snapshot. Modules with no patch, no snapshot, or no version recorded in the
snapshot are skipped.

Exit 0 when consistent, 1 on a mismatch.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# 'bioconda::subread=2.0.1' / '- subread=2.0.1' -> ('subread', '2.0.1')
DEP = re.compile(r"^\s*-\s*(?:[\w-]+::)?([A-Za-z0-9_.-]+)\s*=\s*([\w.+-]+)\s*$")


def patched_modules(modules_json: Path) -> dict[str, str]:
    """Module name -> patch file, for every entry declaring a patch."""
    found: dict[str, str] = {}

    def walk(node, trail):
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if isinstance(value, dict) and "patch" in value:
                found[key] = value["patch"]
            elif isinstance(value, dict):
                walk(value, trail + [key])

    walk(json.loads(modules_json.read_text()), [])
    return found


def declared_versions(env_yml: Path) -> dict[str, str]:
    if not env_yml.is_file():
        return {}
    versions = {}
    for line in env_yml.read_text().splitlines():
        match = DEP.match(line)
        if match:
            versions[match.group(1).lower()] = match.group(2)
    return versions


def snapshot_versions(snap: Path, tools: set[str]) -> dict[str, set[str]]:
    """Version strings the snapshot records for the tools we care about.

    Handles both shapes nf-test produces: {"subread": "2.1.1"} and the
    topic-channel form ["PROCESS", "subread", "2.1.1"].
    """
    if not snap.is_file():
        return {}
    seen: dict[str, set[str]] = {}

    def note(tool, version):
        tool = str(tool).lower()
        if tool in tools and version is not None:
            seen.setdefault(tool, set()).add(str(version))

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (str, int, float)):
                    note(key, value)
                walk(value)
        elif isinstance(node, list):
            if len(node) == 3 and all(isinstance(i, (str, int, float)) for i in node):
                note(node[1], node[2])
            for item in node:
                walk(item)

    walk(json.loads(snap.read_text()))
    return seen


def compatible(declared: str, recorded: str) -> bool:
    """Whether a recorded version is consistent with the declared one.

    Tools often report more detail than the conda pin carries -- bwa pins 0.7.19
    but reports '0.7.19-r1273'. Treat one being a prefix of the other at a version
    boundary as agreement, so only genuine divergence (2.0.1 vs 2.1.1) is flagged.
    """
    declared, recorded = str(declared).strip(), str(recorded).strip()
    if declared == recorded:
        return True
    longer, shorter = (recorded, declared) if len(recorded) > len(declared) else (declared, recorded)
    return longer.startswith(shorter) and not longer[len(shorter)].isdigit()


def main() -> int:
    modules_json = REPO / "modules.json"
    if not modules_json.is_file():
        print("modules.json not found", file=sys.stderr)
        return 1

    patches = patched_modules(modules_json)
    if not patches:
        print("No patched nf-core modules; nothing to check.")
        return 0

    failures = []
    for name, patch in sorted(patches.items()):
        module_dir = REPO / "modules" / "nf-core" / name
        declared = declared_versions(module_dir / "environment.yml")
        snap = module_dir / "tests" / "main.nf.test.snap"

        if not declared:
            print(f"- {name}: patched ({patch}), no parseable environment.yml -- skipped")
            continue
        if not snap.is_file():
            print(f"- {name}: patched, no test snapshot -- skipped")
            continue

        recorded = snapshot_versions(snap, set(declared))
        if not recorded:
            print(f"- {name}: patched, snapshot records no tool version -- skipped")
            continue

        for tool, want in declared.items():
            got = recorded.get(tool)
            if got and not all(compatible(want, v) for v in got):
                failures.append(
                    f"{name}: environment.yml declares {tool}={want} but "
                    f"{snap.relative_to(REPO)} records {sorted(got)}"
                )
            elif got:
                print(f"- {name}: {tool}={want} consistent with snapshot")

    if failures:
        print("\nPatched module snapshots disagree with the patched version:\n", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print(
            "\nRegenerate the module's snapshot against the patched module, or drop the patch.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

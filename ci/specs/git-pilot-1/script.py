# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render the synthetic git-pilot-1 fixture as a deterministic text file.

The fixture is data, not course content: no question text, options, answers, feedback or records are present. The script
uses only the Python standard library so the runtime image needs no extra packages for this pilot.
"""
import json
import pathlib


def main():
    fixture = json.loads(pathlib.Path("/input/fixture.json").read_text(encoding="utf-8"))
    config = json.loads(pathlib.Path("/input/config.json").read_text(encoding="utf-8"))
    lines = [f"repository {fixture['repository']}", f"branch {fixture['branch']}"]
    for commit in fixture["commits"]:
        lines.append(f"commit {commit['id']} {commit['message']}")
    pathlib.Path("/out/git-fixture.txt").write_text("\n".join(lines) + config["newline"], encoding="utf-8")


if __name__ == "__main__":
    main()

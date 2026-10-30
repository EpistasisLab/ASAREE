"""Regenerate saved report requests exported from stats_analyses, without database writes.

python scripts/stats_regenerate.py stats-inputs.json artifacts/reviewed-reports
Saved source/configuration are used unchanged; legacy configs receive the
current documented defaults. A manifest distinguishes failed/incomplete work.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from asaree.services.stats_html import render_html
from asaree.services.stats_source import StatsRequest, compute


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--analysis-id", action="append", help="Limit to these saved report IDs")
    args = parser.parse_args()
    requests = json.loads(args.source.read_text())
    if not isinstance(requests, list):
        parser.error("Expected a JSON array of saved report requests")
    args.destination.mkdir(parents=True, exist_ok=True)
    manifest = dict(input_sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(), analyses=[])

    def save_manifest():
        (args.destination / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    for saved in requests:
        if args.analysis_id and saved["id"] not in args.analysis_id:
            continue
        request = saved["request"]
        # IDs are used only as stable audit keys, never as unchecked output paths.
        stem = hashlib.sha256(str(saved["id"]).encode()).hexdigest()[:16]
        entry = dict(
            id=saved["id"],
            metric=request["config"]["metric"],
            execution_status="incomplete",
            json=f"{stem}.json",
            html=f"{stem}.html",
        )
        manifest["analyses"].append(entry)
        save_manifest()
        try:
            config = StatsRequest.model_validate(request["config"]).model_dump()
            output = compute(request["source"], config)
            analysis = dict(id=saved["id"], created_at=saved["created_at"], output=output)
            (args.destination / entry["json"]).write_text(json.dumps(analysis, sort_keys=True, allow_nan=False) + "\n")
            (args.destination / entry["html"]).write_text(render_html(analysis))
            entry.update(
                execution_status=output["execution_status"],
                inference_status=output["summary"]["status"],
                selected_family=output["family"],
                requested_config=request["config"],
                resolved_config=config,
            )
        except (ValueError, RuntimeError, KeyError) as exc:
            entry.update(execution_status="failed", reason=str(exc))
        finally:
            save_manifest()
        print(f"{entry['metric']}: {entry['execution_status']}")


if __name__ == "__main__":
    main()

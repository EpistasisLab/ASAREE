"""Motoro's OKF server, pointed at ONE user-chosen bundle directory.

Motoro's ``motoro.mcp_servers.okf`` reads its bundle root from
``AGENTIC_OKF_BUNDLE_DIR`` and *never* from a tool argument -- deliberately, so
an agent can't walk out of the bundle it was given (see that module's own
"Configuration" docstring: "a product spawns one server instance per bundle it
wants to expose, and each instance is jailed to exactly that directory"). That
makes "which bundle" a property of the *process*, not of a call, so a
per-researcher bundle can't be a field on a canvas node that one shared
``motoro-okf`` server reads -- it has to be its own registered server.

This module is that per-bundle entry point. It sets the env var from ``--bundle``
and then runs Motoro's own server object unchanged -- no fork, no reimplemented
tools. The path rides in the registration's ``command`` column
(:mod:`asaree.services.okf_bundles`) rather than in an env dict, because
``mcp_server_configs`` has no env column and ``mcp_service.register_server``
takes no ``server_env``: the worker rebuilds its registry from that table
(``hydrate_registry``), so anything not in a column doesn't survive a restart.

The env var is set before the import, not after, purely for clarity -- the
server re-reads ``os.environ`` on every tool call, so it would work either way.
Whatever value this process inherited (the deployment-wide bundle the *system*
``motoro-okf`` server serves, which is in Motoro's default MCP subprocess env
allowlist and so is copied into every child) is overwritten here.
"""

from __future__ import annotations

import argparse
import os


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m asaree.mcp_servers.okf_bundle",
        description="Serve one OKF bundle directory over MCP.",
    )
    parser.add_argument("--bundle", required=True, help="Absolute path to the OKF bundle directory to serve.")
    args = parser.parse_args()

    os.environ["AGENTIC_OKF_BUNDLE_DIR"] = args.bundle

    from motoro.mcp_servers.okf import mcp

    mcp.run()


if __name__ == "__main__":
    main()

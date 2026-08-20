"""Core error type.

Pure computation raises :class:`ComputeError` for invalid inputs (too few
conditions, a missing column, an unknown method). The thin MCP server wrappers
(issue #1457) catch it and render the ``{"error": ...}`` JSON the monolith
returned, keeping the wire contract unchanged while the library itself signals
failure the Pythonic way.
"""

from __future__ import annotations


class ComputeError(Exception):
    """Raised for invalid computation inputs (bad shapes, missing columns, ...)."""

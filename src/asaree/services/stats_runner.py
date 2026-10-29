"""File-free subprocess boundary used by the existing background worker."""

import json
import sys
from contextlib import redirect_stdout

from asaree.services.stats_source import compute


def main():
    request = json.load(sys.stdin)
    # Some numerical libraries print optimizer messages; preserve stdout for JSON.
    with redirect_stdout(sys.stderr):
        output = compute(request["source"], request["config"])
    json.dump(output, sys.stdout, allow_nan=False)


if __name__ == "__main__":
    main()

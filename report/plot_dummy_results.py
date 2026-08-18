# SPDX-License-Identifier: Apache-2.0
"""Compatibility entry point for the normalized dummy report dataset.

Use python -m report.plot_results for new commands.
"""

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from report.plot_results import main

if __name__ == "__main__":
    main()

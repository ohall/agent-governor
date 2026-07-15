"""Shared chart styling: slide-ready, light + dark variants, honesty footers.

Every chart carries its measurement caveat ON the image so the label can
never drift away from the artifact.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

if TYPE_CHECKING:
    from collections.abc import Callable

    from matplotlib.figure import Figure

CHARTS_DIR = Path(__file__).parent / "charts"

PALETTE = ["#2f7ed8", "#e05c5c", "#31a354", "#b48ead", "#e8a33d"]

_VARIANTS = {
    "light": {"bg": "#ffffff", "fg": "#1a1a2e", "grid": "#d9d9e3"},
    "dark": {"bg": "#0f1116", "fg": "#e8e8f0", "grid": "#33364a"},
}


def save_variants(
    build: Callable[[], Figure],
    name: str,
    footnote: str,
    out_dir: Path = CHARTS_DIR,
) -> list[Path]:
    """Render `build()` twice (light/dark) with the honesty footnote baked in."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for variant, colors in _VARIANTS.items():
        with plt.rc_context(
            {
                "figure.facecolor": colors["bg"],
                "axes.facecolor": colors["bg"],
                "savefig.facecolor": colors["bg"],
                "text.color": colors["fg"],
                "axes.labelcolor": colors["fg"],
                "axes.edgecolor": colors["fg"],
                "xtick.color": colors["fg"],
                "ytick.color": colors["fg"],
                "grid.color": colors["grid"],
                "axes.grid": True,
                "grid.linewidth": 0.6,
                "font.size": 14,
                "axes.titlesize": 18,
                "axes.labelsize": 15,
                "legend.fontsize": 12,
                "axes.spines.top": False,
                "axes.spines.right": False,
            }
        ):
            fig = build()
            fig.tight_layout(rect=(0, 0.045, 1, 1))
            fig.text(
                0.01,
                0.008,
                footnote,
                fontsize=8.5,
                alpha=0.75,
                ha="left",
                va="bottom",
            )
            path = out_dir / f"{name}_{variant}.png"
            fig.savefig(path, dpi=200)
            plt.close(fig)
            paths.append(path)
    return paths

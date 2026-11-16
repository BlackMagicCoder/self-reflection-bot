"""Chart rendering for /progress — kept separate so matplotlib stays out of bot.py."""
from datetime import date
from io import BytesIO

import matplotlib
matplotlib.use("Agg")  # headless backend; must be set before pyplot import
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

_LINE = "#4269D0"   # single-series hue
_INK = "#374151"    # primary text
_MUTED = "#6B7280"  # secondary text / axis labels
_GRID = "#E5E7EB"


def render_mood_chart(entries: list[dict], days: int = 90) -> bytes | None:
    """Render the mood log of the last `days` days as a PNG line chart.

    Returns None when there are fewer than 2 plottable datapoints.
    """
    today = date.today()
    points = []
    for e in entries:
        try:
            d = date.fromisoformat(e["date"])
        except (KeyError, TypeError, ValueError):
            continue
        score = e.get("score")
        if (today - d).days <= days and isinstance(score, (int, float)):
            points.append((d, score))
    if len(points) < 2:
        return None
    points.sort(key=lambda p: p[0])
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    ax.plot(
        xs, ys,
        color=_LINE, linewidth=2,
        marker="o", markersize=6,
        markerfacecolor=_LINE, markeredgecolor="white", markeredgewidth=1.5,
    )

    ax.set_ylim(0.5, 10.5)
    ax.set_yticks(range(1, 11))
    ax.set_title(f"Stimmungsverlauf (letzte {days} Tage)", color=_INK, fontsize=12, loc="left")
    ax.set_ylabel("Stimmung (1–10)", color=_MUTED, fontsize=9)

    ax.grid(axis="y", color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(_GRID)
    ax.tick_params(colors=_MUTED, labelsize=8, length=0)

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m."))
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=8))

    buf = BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()

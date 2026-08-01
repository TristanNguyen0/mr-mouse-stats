"""Build-time inline SVG charts — no JS, no external assets."""

from __future__ import annotations

from xml.sax.saxutils import escape

_LABEL_WIDTH = 190
_COUNT_WIDTH = 34
_BAR_HEIGHT = 20
_GAP = 8
_FONT = "font-family='system-ui, sans-serif' font-size='13'"


def bar_chart(items: list[tuple[str, int]], width: int = 640) -> str:
    """Horizontal bar chart: label | bar | count. Empty items -> ''."""
    if not items:
        return ""
    max_count = max(count for _, count in items)
    bar_span = width - _LABEL_WIDTH - _COUNT_WIDTH
    height = len(items) * (_BAR_HEIGHT + _GAP) - _GAP
    parts = [
        f"<svg viewBox='0 0 {width} {height}' width='{width}' height='{height}' "
        f"role='img' xmlns='http://www.w3.org/2000/svg'>"
    ]
    for i, (label, count) in enumerate(items):
        y = i * (_BAR_HEIGHT + _GAP)
        bar = max(2, round(bar_span * count / max_count))
        text_y = y + _BAR_HEIGHT - 5
        parts.append(
            f"<text x='{_LABEL_WIDTH - 8}' y='{text_y}' text-anchor='end' "
            f"{_FONT}>{escape(label)}</text>"
            f"<rect x='{_LABEL_WIDTH}' y='{y}' width='{bar}' "
            f"height='{_BAR_HEIGHT}' rx='3' fill='#4a7db5'/>"
            f"<text x='{_LABEL_WIDTH + bar + 6}' y='{text_y}' {_FONT} "
            f"fill='#555'>{count}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)

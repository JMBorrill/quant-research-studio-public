"""Deterministic SVG figures for analyses (Stage 5 decision 5): line, bar, scatter, heatmap and
estimate-with-interval charts, drawn in Python with no dependency. The same numbers always give the
same bytes: fixed size and font names, coordinates rounded to 0.01, every text escaped, and no
timestamps or IDs. The studio shows them as images, so nothing in them can run."""

import math
from collections.abc import Sequence
from html import escape
from itertools import cycle

WIDTH, HEIGHT = 640, 360
LEFT, RIGHT, TOP, BOTTOM = 72, 16, 36, 48  # margins around the plot area
FONT = "DejaVu Sans, Arial, sans-serif"
INK, MUTED, GRID = "#1f2933", "#616e7c", "#d9dee3"
SERIES = ("#2b6cb0", "#c05621", "#2f855a", "#6b46c1")  # one colour per series, in order


def _n(value: float) -> str:
    """A coordinate rounded to 0.01, without a trailing zero or a negative zero."""
    text = f"{round(value, 2) + 0.0:.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def label(value: float) -> str:
    """An axis or value label: four significant figures."""
    return f"{value:.4g}"


def _text(x: float, y: float, text: str, anchor: str = "middle", size: int = 11, fill: str = MUTED, rotate: bool = False) -> str:
    turn = f' transform="rotate(-90 {_n(x)} {_n(y)})"' if rotate else ""
    printable = "".join(c for c in text if c >= " ")  # control characters are not allowed in XML
    return f'<text x="{_n(x)}" y="{_n(y)}" font-size="{size}" text-anchor="{anchor}" fill="{fill}"{turn}>{escape(printable)}</text>'


class _Axes:
    """The plot area and the linear maps from data to it."""

    def __init__(self, x_range: tuple[float, float], y_range: tuple[float, float]):
        self.x0, self.x1 = _padded(*x_range, pad=0.0)
        self.y0, self.y1 = _padded(*y_range, pad=0.05)

    def x(self, value: float) -> float:
        return LEFT + (value - self.x0) / (self.x1 - self.x0) * (WIDTH - LEFT - RIGHT)

    def y(self, value: float) -> float:
        return HEIGHT - BOTTOM - (value - self.y0) / (self.y1 - self.y0) * (HEIGHT - TOP - BOTTOM)

    def grid(self) -> list[str]:
        """Horizontal lines at the bottom, middle and top of the y range, labelled, and zero if in range."""
        lines = []
        levels = [self.y0, (self.y0 + self.y1) / 2, self.y1] + ([0.0] if self.y0 < 0 < self.y1 else [])
        for level in levels:
            y = self.y(level)
            stroke = MUTED if level == 0 else GRID
            lines.append(f'<line x1="{LEFT}" y1="{_n(y)}" x2="{WIDTH - RIGHT}" y2="{_n(y)}" stroke="{stroke}" stroke-width="1"/>')
            lines.append(_text(LEFT - 6, y + 4, label(level), anchor="end"))
        return lines


def _padded(low: float, high: float, pad: float) -> tuple[float, float]:
    if not (math.isfinite(low) and math.isfinite(high)):
        raise ValueError("A figure needs finite values.")
    if low == high:
        return low - 1, high + 1
    margin = (high - low) * pad
    return low - margin, high + margin


def _svg(title: str, x_title: str, y_title: str, body: list[str]) -> str:
    head = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" font-family="{FONT}">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="#ffffff"/>',
        _text(WIDTH / 2, 20, title, size=13, fill=INK),
        _text(LEFT + (WIDTH - LEFT - RIGHT) / 2, HEIGHT - 8, x_title),
        _text(14, TOP + (HEIGHT - TOP - BOTTOM) / 2, y_title, rotate=True),
    ]
    return "\n".join([*head, *body, "</svg>"]) + "\n"


def _finite(values: Sequence[float]) -> list[float]:
    return [v for v in values if v is not None and math.isfinite(v)]


def line(title: str, x_labels: Sequence[str], series: dict[str, Sequence[float]], y_title: str) -> str:
    """One or more series over the same ordered x positions; missing values break a line."""
    ys = _finite([v for values in series.values() for v in values])
    axes = _Axes((0, max(len(x_labels) - 1, 1)), (min(ys), max(ys)) if ys else (0, 1))
    body = axes.grid()
    for colour, values in zip(cycle(SERIES), series.values()):
        segment = []
        for i, value in enumerate([*values, None]):  # the closing None ends the last segment
            if value is not None and math.isfinite(value):
                segment.append(f"{_n(axes.x(i))},{_n(axes.y(value))}")
            elif segment:
                body.append(f'<polyline points="{" ".join(segment)}" fill="none" stroke="{colour}" stroke-width="1.5"/>')
                segment = []
    body += _x_ends(axes, x_labels) + _legend(series)
    return _svg(title, "", y_title, body)


def bar(title: str, labels: Sequence[str], values: Sequence[float], y_title: str) -> str:
    """One bar per label, from zero."""
    finite = _finite(values)
    axes = _Axes((0, len(labels)), (min([0.0, *finite]), max([0.0, *finite])))
    body = axes.grid()
    width = (WIDTH - LEFT - RIGHT) / max(len(labels), 1)
    for i, (name, value) in enumerate(zip(labels, values, strict=True)):
        if value is not None and math.isfinite(value):
            top, bottom = sorted((axes.y(value), axes.y(0.0)))
            body.append(f'<rect x="{_n(axes.x(i) + width * 0.1)}" y="{_n(top)}" width="{_n(width * 0.8)}" height="{_n(bottom - top)}" fill="{SERIES[0]}"/>')
        if len(labels) <= 24:
            body.append(_text(axes.x(i) + width / 2, HEIGHT - BOTTOM + 14, name))
    return _svg(title, "", y_title, body)


def scatter(title: str, x: Sequence[float], y: Sequence[float], x_title: str, y_title: str) -> str:
    """Points (x, y); pairs with a missing value are left out."""
    pairs = [(a, b) for a, b in zip(x, y, strict=True) if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)]
    xs, ys = [a for a, _ in pairs] or [0.0], [b for _, b in pairs] or [0.0]
    axes = _Axes(_padded(min(xs), max(xs), pad=0.05), (min(ys), max(ys)))
    body = axes.grid() + [f'<circle cx="{_n(axes.x(a))}" cy="{_n(axes.y(b))}" r="2" fill="{SERIES[0]}" fill-opacity="0.5"/>' for a, b in pairs]
    body += [_text(LEFT, HEIGHT - BOTTOM + 14, label(axes.x0), anchor="start"), _text(WIDTH - RIGHT, HEIGHT - BOTTOM + 14, label(axes.x1), anchor="end")]
    return _svg(title, x_title, y_title, body)


def heatmap(title: str, rows: Sequence[str], columns: Sequence[str], values: Sequence[Sequence[float]], x_title: str, y_title: str) -> str:
    """A grid of cells shaded from blue (lowest) through white to orange (highest), each labelled."""
    finite = _finite([v for row in values for v in row])
    low, high = (min(finite), max(finite)) if finite else (0.0, 1.0)
    cell_w, cell_h = (WIDTH - LEFT - RIGHT) / max(len(columns), 1), (HEIGHT - TOP - BOTTOM) / max(len(rows), 1)
    body = []
    for r, (name, row) in enumerate(zip(rows, values, strict=True)):
        body.append(_text(LEFT - 6, TOP + (r + 0.5) * cell_h + 4, name, anchor="end"))
        for c, value in enumerate(row):
            x, y = LEFT + c * cell_w, TOP + r * cell_h
            fill = _shade(value, low, high)
            body.append(f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(cell_w)}" height="{_n(cell_h)}" fill="{fill}" stroke="#ffffff"/>')
            if value is not None and math.isfinite(value) and len(columns) <= 12 and len(rows) <= 12:
                body.append(_text(x + cell_w / 2, y + cell_h / 2 + 4, label(value), size=10, fill=INK))
    body += [_text(LEFT + (c + 0.5) * cell_w, HEIGHT - BOTTOM + 14, name) for c, name in enumerate(columns)]
    return _svg(title, x_title, y_title, body)


def _shade(value: float | None, low: float, high: float) -> str:
    if value is None or not math.isfinite(value):
        return "#eeeeee"
    t = 0.5 if high == low else (value - low) / (high - low)
    near, far = ((43, 108, 176), (255, 255, 255)) if t < 0.5 else ((255, 255, 255), (192, 86, 33))
    s = t * 2 if t < 0.5 else t * 2 - 1
    return "#" + "".join(f"{round(a + (b - a) * s):02x}" for a, b in zip(near, far, strict=True))


def intervals(title: str, labels: Sequence[str], estimates: Sequence[float], lows: Sequence[float], highs: Sequence[float], y_title: str) -> str:
    """Each estimate as a point with its interval as a line."""
    finite = _finite([*estimates, *lows, *highs])
    axes = _Axes((0, len(labels)), (min([0.0, *finite]), max([0.0, *finite])))
    body = axes.grid()
    for i, (name, estimate, low, high) in enumerate(zip(labels, estimates, lows, highs, strict=True)):
        x = axes.x(i + 0.5)
        if low is not None and high is not None and math.isfinite(low) and math.isfinite(high):
            body.append(f'<line x1="{_n(x)}" y1="{_n(axes.y(low))}" x2="{_n(x)}" y2="{_n(axes.y(high))}" stroke="{SERIES[0]}" stroke-width="2"/>')
        if estimate is not None and math.isfinite(estimate):
            body.append(f'<circle cx="{_n(x)}" cy="{_n(axes.y(estimate))}" r="3.5" fill="{SERIES[1]}"/>')
        body.append(_text(x, HEIGHT - BOTTOM + 14, name))
    return _svg(title, "", y_title, body)


def _x_ends(axes: _Axes, x_labels: Sequence[str]) -> list[str]:
    if not x_labels:
        return []
    return [_text(LEFT, HEIGHT - BOTTOM + 14, x_labels[0], anchor="start"), _text(WIDTH - RIGHT, HEIGHT - BOTTOM + 14, x_labels[-1], anchor="end")]


def _legend(series: dict) -> list[str]:
    if len(series) < 2:
        return []
    return [_text(WIDTH - RIGHT, TOP + 12 * (i + 1), name, anchor="end", fill=colour) for i, (name, colour) in enumerate(zip(series, cycle(SERIES)))]

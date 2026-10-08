"""Step 1 tells a sheet of poses apart from a character or a turnaround."""

from __future__ import annotations

from PIL import Image, ImageDraw

from core.views import ACTION_SHEET_MIN, figure_count


def _figures(cols: int, rows: int, gap: int = 6) -> Image.Image:
    w, h = 40, 90
    img = Image.new("RGBA", (cols * (w + gap), rows * (h + gap)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for r in range(rows):
        for c in range(cols):
            x, y = c * (w + gap), r * (h + gap)
            d.ellipse([x + 10, y, x + 30, y + 20], fill=(90, 160, 60, 255))      # head
            d.rectangle([x + 5, y + 20, x + 35, y + h], fill=(120, 80, 50, 255))  # body
    return img


def test_turnaround_is_not_an_action_sheet():
    assert figure_count(_figures(4, 1, gap=40)) == 4
    assert figure_count(_figures(4, 1, gap=40)) < ACTION_SHEET_MIN


def test_action_grid_is_an_action_sheet():
    assert figure_count(_figures(16, 5)) >= ACTION_SHEET_MIN


def test_single_character():
    assert figure_count(_figures(1, 1)) == 1

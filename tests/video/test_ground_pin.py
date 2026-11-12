# SPDX-License-Identifier: Apache-2.0
"""`video-loop --ground-pin on`: every cell of a walk stands on one ground line, moved whole, pixels as they were."""

from __future__ import annotations

import numpy as np
from PIL import Image

from sprite_gen.video import loop


def _figure(bottom: int, size=(60, 120)) -> Image.Image:
    """A body 50 px tall whose feet end on `bottom`, with a 1 px speck under it (a speck is not the ground)."""
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    im.paste(Image.new("RGBA", (20, 50), (90, 90, 120, 255)), (20, bottom - 50))
    im.putpixel((5, size[1] - 1), (255, 255, 255, 255))
    return im


def test_each_frame_moves_up_whole_onto_the_highest_ground_row():
    frames = [_figure(b) for b in (100, 108, 104, 100)]
    pinned, record = loop.ground_pin_frames(frames)
    assert record["applied"] and record["ground_row"] == 99 and record["spread_px"] == 8
    assert record["dy_px"] == [0, -8, -4, 0]
    assert [loop.lowest_solid_row(f) for f in pinned] == [99, 99, 99, 99]
    # moved, not redrawn: the body's pixels are the frame's own, shifted
    a = np.asarray(frames[1])[58:108]
    b = np.asarray(pinned[1])[50:100]
    assert np.array_equal(a[:, 20:40], b[:, 20:40])


def test_a_cut_already_on_one_ground_is_left_as_it_is():
    frames = [_figure(100) for _ in range(3)]
    pinned, record = loop.ground_pin_frames(frames)
    assert record["dy_px"] == [0, 0, 0] and all(p is f for p, f in zip(pinned, frames))


def test_an_empty_cut_is_not_pinned():
    frames = [Image.new("RGBA", (10, 10), (0, 0, 0, 0))]
    pinned, record = loop.ground_pin_frames(frames)
    assert not record["applied"] and pinned == frames

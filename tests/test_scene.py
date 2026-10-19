"""Checks for the drawing of the video. Run with `python tests/test_scene.py` or pytest."""

import numpy as np
from manim import RIGHT, Text

import ibis_anim._scene  # noqa: F401  the import makes the copies of the glyphs share their paths


def test_a_copy_of_a_text_shares_the_paths_of_its_glyphs():
    text = Text("store_revenue", font="Consolas", font_size=15)
    copy = text.copy()
    assert all(c.path_obj is t.path_obj for c, t in zip(copy, text))  # a deep copy of each path made copies 8 times slower
    copy.shift(RIGHT)
    assert not np.allclose(copy[0].points, text[0].points)  # the points of the copy are its own


if __name__ == "__main__":
    test_a_copy_of_a_text_shares_the_paths_of_its_glyphs()
    print("ok")

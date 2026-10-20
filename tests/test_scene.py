"""Checks for the drawing of the video. Run with `python tests/test_scene.py` or pytest."""

import numpy as np
from manim import RIGHT, Scene, Text

import ibis_anim
import ibis_anim._scene  # noqa: F401  the import makes the copies of the glyphs share their paths


def test_a_copy_of_a_text_shares_the_paths_of_its_glyphs():
    text = Text("store_revenue", font="Consolas", font_size=15)
    copy = text.copy()
    assert all(c.path_obj is t.path_obj for c, t in zip(copy, text))  # a deep copy of each path made copies 8 times slower
    copy.shift(RIGHT)
    assert not np.allclose(copy[0].points, text[0].points)  # the points of the copy are its own


def test_a_play_with_no_animation_does_nothing():
    from ibis_anim._scene import DebuggerScene

    calls, real = [], Scene.play
    Scene.play = lambda self, *animations, **kwargs: calls.append(animations)
    try:
        scene = DebuggerScene.__new__(DebuggerScene)
        DebuggerScene.play(scene, *[], run_time=1.2)  # e.g. a distinct union whose dropped rows are all at the end: no row moves
        DebuggerScene.play(scene, "an animation")
    finally:
        Scene.play = real
    assert calls == [("an animation",)], calls  # manim raises "Called Scene.play with no animations" on an empty play


def test_sort_keys_give_the_column_and_the_direction():
    from ibis_anim._scene import _sort_keys

    t = ibis_anim.table({"a": "int64", "b": "string"}, name="t")
    assert _sort_keys(t.order_by([t.a.desc(), "b"]).op().keys) == [("a", False), ("b", True)]
    window = t.mutate(r=t.a.sum().over(order_by=t.b.desc())).op().values["r"]  # ibis 10 and 11 call the field of a key `expr`
    assert _sort_keys(window.order_by) == [("b", False)]


def test_a_coalesce_on_a_join_runs_on_the_rows_of_the_join():
    from ibis_anim._derive import derive
    from ibis_anim._samples import for_step
    from ibis_anim._scene import _evaluate, _run_on_samples
    from ibis_anim._scope import table_names

    orders = ibis_anim.table({"order_id": "int64", "customer_id": "int64", "name": "string"}, name="orders")
    customers = ibis_anim.table({"customer_id": "int64", "name": "string"}, name="customers")

    def pipeline():
        joined = orders.join(customers, "customer_id")  # both tables have `name`: the join calls the right one `name_right`
        return joined.select("order_id", label=ibis_anim.coalesce(joined.name_right, joined.name))

    _, steps = ibis_anim.trace(pipeline)
    step = steps[-1]
    given = {op: (rows, False) for op, rows in for_step(step, derive(steps, table_names(steps))).items()}
    ins, _, _ = _run_on_samples(step, given)
    args = list(step.output.op().values["label"].arg)
    found = _evaluate(args, step.inputs[0], ins[0].rows)  # an IntegrityError before the fix: the fields of the join tables
    assert ins[0].rows and found == [[r["name_right"], r["name"]] for r in ins[0].rows], (found, ins[0].rows)


if __name__ == "__main__":
    test_a_copy_of_a_text_shares_the_paths_of_its_glyphs()
    test_a_play_with_no_animation_does_nothing()
    test_sort_keys_give_the_column_and_the_direction()
    test_a_coalesce_on_a_join_runs_on_the_rows_of_the_join()
    print("ok")

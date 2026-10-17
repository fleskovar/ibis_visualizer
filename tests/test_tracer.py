"""Checks for the drop-in module and the tracer. Run with `python tests/test_tracer.py` or pytest."""

import ibis

import ibis_anim
from ibis_anim import _


def pipeline(t, only_big: bool):
    if only_big:
        t = t.filter(t.a > 1)
    return t.group_by("b").aggregate(total=_.a.sum())


def test_trace_records_the_path_that_ran():
    t = ibis_anim.memtable({"a": [1, 2, 3], "b": ["x", "y", "x"]})
    _, steps = ibis_anim.trace(pipeline, t, True)
    assert [s.op for s in steps] == ["filter", "group_by → aggregate"]
    assert steps[0].code == "t.filter(t.a > 1)" and steps[0].used == ["a"]
    assert steps[1].group_by == ["b"] and steps[1].created == ["total"] and steps[1].labels == ["t"]
    assert steps[1].stack[-1].name == "pipeline"
    _, steps = ibis_anim.trace(pipeline, t, False)
    assert [s.op for s in steps] == ["group_by → aggregate"]


def test_methods_called_on_a_join_are_recorded():
    left = ibis_anim.memtable({"k": [1, 2], "a": [3, 4]})
    right = ibis_anim.memtable({"k": [1, 2], "b": [5, 6]})
    steps = ibis_anim.trace(lambda: left.join(right, "k").mutate(c=_.a + _.b).filter(_.c > 8))[1]
    assert [s.op for s in steps] == ["join", "mutate", "filter"]
    assert steps[1].labels == ["join result"] and steps[1].created == ["c"]


def named_columns(t):
    label = ibis_anim.coalesce(t.b, "none")
    points = (t.a * 10).cast("int64")
    return t.mutate(label=label, points=points, inline=t.a + 1)


def test_columns_built_outside_a_table_call_are_steps():
    t = ibis_anim.memtable({"a": [1, 2], "b": ["x", None]})
    steps = ibis_anim.trace(named_columns, t)[1]
    assert [s.op for s in steps] == ["column", "column", "mutate"]  # `inline` is part of the mutate
    label, points, mutate = steps
    assert label.code == 'label = ibis_anim.coalesce(t.b, "none")' and label.output.columns == ("label",)
    assert points.stack[-1].lineno == label.stack[-1].lineno + 1  # each column has the line of its assignment
    assert mutate.labels == ["t", "label", "points"] and mutate.inputs[1].op() == label.output.op()


def test_drop_in_exposes_ibis():
    import ibis_anim.selectors as s

    assert s is ibis.selectors and ibis.selectors.__spec__.name == "ibis.selectors"
    assert ibis_anim.Table is ibis.Table and ibis_anim.memtable is ibis.memtable


if __name__ == "__main__":
    test_trace_records_the_path_that_ran()
    test_methods_called_on_a_join_are_recorded()
    test_columns_built_outside_a_table_call_are_steps()
    test_drop_in_exposes_ibis()
    print("ok")

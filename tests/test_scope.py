"""Checks for the tables panel plan. Run with `python tests/test_scope.py` or pytest."""

import ibis_anim
from ibis_anim import _
from ibis_anim._scope import chains, plan, table_names


def pipeline(a, b):
    a = a.filter(_.x > 0)  # 0: rebinds a
    joined = a.join(b, "x")  # 1: a is not read again, so joined takes its place
    joined = joined.mutate(y=_.x * 2)  # 2
    return joined.select("x", "y")  # 3


def chained(a, b):
    return a.filter(_.x > 1).join(b, "x").mutate(w=_.x * 3)  # one statement, three steps


def test_plan_rebinds_and_expires_tables():
    a = ibis_anim.memtable({"x": [1, -1, 2]})
    b = ibis_anim.memtable({"x": [1, 2], "z": [3, 4]})
    steps = ibis_anim.trace(pipeline, a, b)[1]
    frames, names = plan(steps, keep_idle=2), table_names(steps)
    a0, a1, joined, mutated, selected = a.op(), steps[0].output.op(), steps[1].output.op(), steps[2].output.op(), steps[3].output.op()
    assert [names[k] for k in (a0, a1, joined, mutated)] == ["a", "a", "joined", "joined"]
    assert frames[0].shown == [a0] and frames[0].after == [a1] and frames[0].replaced == a0
    assert frames[1].shown == [a1, b.op()] and frames[1].after == [joined, b.op()]
    assert frames[2].after == [mutated, b.op()]  # b was last read at step 1: it stays for 2 steps
    assert frames[1].dead_after == {b.op()} and frames[2].dead_after == {b.op()}  # dim at once, leave later
    assert frames[3].dead == {b.op()} and frames[3].expired == [b.op()] and frames[3].after == [selected]


def test_chains_group_one_statement():
    a = ibis_anim.memtable({"x": [1, -1, 2]})
    b = ibis_anim.memtable({"x": [1, 2], "z": [3, 4]})
    steps = ibis_anim.trace(chained, a, b)[1] + ibis_anim.trace(pipeline, a, b)[1]
    assert chains(steps, table_names(steps)) == [[0, 1, 2], [3], [4], [5], [6]]


if __name__ == "__main__":
    test_plan_rebinds_and_expires_tables()
    test_chains_group_one_statement()
    print("ok")

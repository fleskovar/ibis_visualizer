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


def spread(a):
    return (
        a.filter(_.x > 0)
        .mutate(y=_.x)
    )


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


def test_unnamed_results_are_named_after_their_operations():
    a = ibis_anim.memtable({"x": [1, -1, 2]})
    b = ibis_anim.memtable({"x": [1, 2], "z": [3, 4]})
    steps = ibis_anim.trace(chained, a, b)[1]
    names = [table_names(steps)[s.output.op()] for s in steps]
    assert names == ["filtered a", "filtered a × b", "filtered a × b with w"]  # never identifiers: chains stay chains


def test_the_code_bar_shows_the_whole_statement():
    from ibis_anim._scene import _code_lines

    steps = ibis_anim.trace(spread, ibis_anim.memtable({"x": [1, -1, 2]}))[1]
    lines = _code_lines(__file__, steps[1].stack[-1].lineno, 40)  # the mutate, on the third line of the statement
    assert [(text, now) for _, text, now in lines] == [
        ("return (", False), ("    a.filter(_.x > 0)", False), ("    .mutate(y=_.x)", True), (")", False)]
    narrow = _code_lines(__file__, steps[1].stack[-1].lineno, 12)
    assert all(len(text) <= 12 for _, text, _ in narrow) and any(now for *_, now in narrow)


if __name__ == "__main__":
    test_plan_rebinds_and_expires_tables()
    test_chains_group_one_statement()
    test_unnamed_results_are_named_after_their_operations()
    test_the_code_bar_shows_the_whole_statement()
    print("ok")

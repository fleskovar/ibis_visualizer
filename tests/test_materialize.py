"""Checks for the tables that hold the output of each step on real data. Run with `python tests/test_materialize.py` or pytest."""

import ibis

import ibis_anim
from ibis_anim import _
from ibis_anim._derive import _substitute
from ibis_anim._scene import _materialize, _run_on_samples

orders = ibis_anim.memtable({"order_id": [1, 2, 3, 4], "customer_id": [1, 2, 1, 3], "amount": [50.0, 150.0, 250.0, 90.0]})
customers = ibis_anim.memtable({"customer_id": [1, 2, 3], "region": ["EU", "US", "EU"]})


def pipeline():
    big = orders.filter(_.amount > 60).mutate(double=_.amount * 2)
    return big.join(customers, "customer_id").group_by("region").aggregate(total=_.double.sum())


def test_each_step_runs_once_on_the_tables_of_the_steps_before():
    steps = ibis_anim.trace(pipeline)[1]
    computed = _materialize(steps)
    assert set(computed) == {s.output.op() for s in steps}
    last = steps[-1].output.op()
    sql = ibis.to_sql(_substitute(last, {k: v for k, v in computed.items() if k != last}).to_expr())
    assert "memtable" not in sql  # the last step reads the table of the join, not the plan from the sources
    for step in steps:  # the same rows and counts as on the full plan
        ins, out, _rows = _run_on_samples(step, {}, computed)
        plain_ins, plain_out, _rows = _run_on_samples(step, {})
        for a, b in zip([*ins, out], [*plain_ins, plain_out]):
            assert a.total == b.total and sorted(map(repr, a.rows)) == sorted(map(repr, b.rows)), step.op


def _rows(pipeline):
    """The rows of the last step, as DebuggerScene._animate gets them: (step, (inputs, result, arrow, caption))."""
    from ibis_anim._derive import derive
    from ibis_anim._samples import for_step
    from ibis_anim._scene import _rows_for_step
    from ibis_anim._scope import table_names

    _, steps = ibis_anim.trace(pipeline)
    facts, computed = derive(steps, table_names(steps)), _materialize(steps)
    given = {op: (rows, False) for op, rows in for_step(steps[-1], facts, computed).items()}
    return steps[-1], _rows_for_step(steps[-1], given, computed, facts)


many = ibis_anim.memtable({"order_id": list(range(1, 21)), "amount": [5.0] * 6 + [500.0] * 14,
                           "store_id": list(range(10, 16)) + [1, 2, 3] * 4 + [1, 2]})  # the first 6 rows pass no test below
stores = ibis_anim.memtable({"store_id": [1, 2, 3], "name": ["A", "B", "C"]})


def test_a_step_whose_first_rows_reach_no_result_gets_rows_that_do():
    for pipeline in (lambda: many.filter(many.amount > 100), lambda: many.join(stores, "store_id")):
        _, (ins, out, _, caption) = _rows(pipeline)
        assert out.rows and out.total == 14, (out.rows, out.total)  # before the fix: no row, while the full result has 14
        assert ins[0].total == 20 and caption, (ins[0].total, caption)  # the counts are of the full tables; a caption says why
        assert {r["amount"] > 100 for r in ins[0].rows} == {True, False}, ins[0].rows  # rows that reach it, and rows that do not


def test_a_result_that_is_empty_for_any_data_says_so():
    for pipeline in (lambda: many.limit(0), lambda: many.filter(many.amount > 10_000)):
        _, (ins, out, _, caption) = _rows(pipeline)
        assert not out.rows and out.total == 0 and "for any data" in (caption or ""), caption
        assert [r["order_id"] for r in ins[0].rows] == [1, 2, 3, 4, 5, 6]  # no search for other rows


if __name__ == "__main__":
    test_each_step_runs_once_on_the_tables_of_the_steps_before()
    test_a_step_whose_first_rows_reach_no_result_gets_rows_that_do()
    test_a_result_that_is_empty_for_any_data_says_so()
    print("ok")

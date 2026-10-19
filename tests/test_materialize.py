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


if __name__ == "__main__":
    test_each_step_runs_once_on_the_tables_of_the_steps_before()
    print("ok")

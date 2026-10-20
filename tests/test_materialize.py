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
    impossible = (lambda: many.limit(0), lambda: many.filter((many.amount > 100) & (many.amount < 50)))
    for pipeline in impossible:  # the facts prove it: no data gives a row
        step, (ins, out, rows, caption) = _rows(pipeline)
        assert not out.rows and out.total == 0 and "for any data" in (caption or ""), caption
        assert [r["order_id"] for r in ins[0].rows] == [1, 2, 3, 4, 5, 6]  # no search for other rows
    step, (ins, out, rows, caption) = _rows(lambda: many.filter(many.amount > 10_000))  # possible: only this data has no such row
    assert not out.rows and "this data" in (caption or "") and "any data" not in caption, caption
    no_rows = ibis_anim.memtable({"order_id": [1], "amount": [1.0]}).filter(_.order_id > 5)
    step, (ins, out, rows, caption) = _rows(lambda: no_rows.mutate(double=no_rows.amount * 2))  # an empty input: before, no caption
    assert not out.rows and "has no row" in (caption or ""), caption


def _rows_of_each_step(pipeline):
    """The rows of each step, with the rows of a chain handed on, as DebuggerScene._animate gets them."""
    from ibis_anim._derive import derive
    from ibis_anim._samples import for_step
    from ibis_anim._scene import _rows_for_step
    from ibis_anim._scope import table_names

    _, steps = ibis_anim.trace(pipeline)
    facts, computed = derive(steps, table_names(steps)), _materialize(steps)
    chain, found = {}, []
    for step in steps:
        made = {op: (rows, False) for op, rows in for_step(step, facts, computed).items()}
        ins, out, rows, caption = _rows_for_step(step, made or chain, computed, facts)
        chain = {step.output.op(): (rows, out.partial)}
        found.append((ins, out, caption))
    return found


def test_rows_that_reach_the_result_are_found_also_with_nulls_and_renamed_columns():
    nulls = ibis_anim.memtable({"order_id": list(range(1, 13)), "amount": [5.0] * 6 + [500.0] * 6,
                                "note": ["a"] * 6 + [None] * 6})  # each row that passes has a null: equality never matches null
    step, (ins, out, rows, caption) = _rows(lambda: nulls.filter(nulls.amount > 100))
    assert out.rows and all(r["note"] is None for r in out.rows), (out.rows, caption)
    assert {r["amount"] > 100 for r in ins[0].rows} == {True, False}, ins[0].rows  # rows that reach it, and rows that do not
    many_stores = ibis_anim.memtable({"store_id": [4, 5, 6, 7, 8, 9, 1, 2, 3], "name": list("DEFGHIABC")})
    step, (ins, out, rows, caption) = _rows(lambda: many.join(many_stores, "store_id"))  # both have `name`: the right one is name_right
    assert out.rows, caption
    reaching = {r["store_id"] for r in out.rows}
    assert reaching <= {r["store_id"] for r in ins[1].rows} and reaching <= {1, 2, 3}, (ins[1].rows, out.rows)
    assert {r["store_id"] in (1, 2, 3) for r in ins[1].rows} == {True, False}, ins[1].rows  # stores that reach it, and not


def test_rows_of_an_aggregate_keep_their_groups_whole():
    for pipeline in (lambda: many.group_by("store_id").having(_.count() > 3).aggregate(n=_.count()),
                     lambda: many.group_by(k=many.store_id).having(_.count() > 3).aggregate(n=_.count())):  # a renamed key
        step, (ins, out, rows, caption) = _rows(pipeline)
        assert out.rows and all(r["n"] > 3 for r in out.rows), (out.rows, caption)  # before: rows of a group were split


def test_a_chain_keeps_its_rows_and_the_counts_of_the_full_tables():
    (ins1, first, caption1), (ins, out, caption) = _rows_of_each_step(lambda: many.filter(many.amount > 100).mutate(double=_.amount * 2))
    assert first.total == 14 and ins[0].total == 14 and out.total == 14, (first.total, ins[0].total, out.total)  # before: 4
    shown = [r["order_id"] for r in many.head(6).to_pyarrow().to_pylist()]
    (ins1, first, caption1), (ins, out, caption) = _rows_of_each_step(lambda: many.mutate(one=1).filter(_.amount > 100))
    kept = [r["order_id"] for r in ins[0].rows if r["order_id"] in shown]
    assert out.rows and caption and len(kept) >= 2, (ins[0].rows, caption)  # rows already on the screen stay in the chain
    assert ins[0].total == 20, ins[0].total


def test_split_tells_rows_that_reach_the_result_from_rows_that_do_not():
    from ibis_anim._scene import _split

    nulls = ibis_anim.memtable({"order_id": list(range(1, 13)), "amount": [5.0] * 6 + [500.0] * 6, "note": ["a"] * 6 + [None] * 6})
    kept = nulls.filter(nulls.amount > 100)  # each row that passes has a null note: SQL `=` never matches a null
    reach, miss = _split(kept.op(), nulls.op(), nulls, kept)
    assert reach and all(r["amount"] > 100 for r in reach), reach
    assert miss and all(r["amount"] < 100 for r in miss), miss  # before: the passing rows with a null counted as misses
    some_stores = ibis_anim.memtable({"store_id": [4, 5, 6, 7, 8, 9, 1, 2, 3], "name": list("DEFGHIABC")})
    joined = many.join(some_stores, "store_id")  # both have `name`: the name of the store is `name_right` in the result
    reach, miss = _split(joined.op(), some_stores.op(), some_stores, joined)
    assert reach and {r["store_id"] for r in reach} <= {1, 2, 3}, reach  # before: `name` compared with the name of an order
    assert miss and all(r["store_id"] not in (1, 2, 3) for r in miss), miss
    groups = many.group_by(k=many.store_id).having(_.count() > 3).aggregate(n=_.count())  # a renamed key
    reach, miss = _split(groups.op(), many.op(), many, groups)
    sizes = {k: sum(r["store_id"] == k for r in reach) for k in {r["store_id"] for r in reach}}
    assert reach and all(n > 3 for n in sizes.values()), reach  # whole groups: each group in reach has its 4 or more rows
    assert all(r["store_id"] not in (1, 2) for r in miss), miss  # stores 1 and 2 have more than 3 orders


if __name__ == "__main__":
    test_each_step_runs_once_on_the_tables_of_the_steps_before()
    test_a_step_whose_first_rows_reach_no_result_gets_rows_that_do()
    test_a_result_that_is_empty_for_any_data_says_so()
    test_rows_that_reach_the_result_are_found_also_with_nulls_and_renamed_columns()
    test_rows_of_an_aggregate_keep_their_groups_whole()
    test_split_tells_rows_that_reach_the_result_from_rows_that_do_not()
    test_a_chain_keeps_its_rows_and_the_counts_of_the_full_tables()
    print("ok")

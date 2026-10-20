"""Checks for the rows that each step generates for tables with a schema only. Run with `python tests/test_samples.py` or pytest."""

import ibis

import ibis_anim
from ibis_anim import _
from ibis_anim._derive import derive
from ibis_anim._samples import for_step
from ibis_anim._scope import table_names

orders = ibis_anim.table({"order_id": "int64", "customer_id": "int64", "amount": "float64", "status": "string"}, name="orders")
customers = ibis_anim.table({"customer_id": "int64", "region": "string", "code": "string"}, name="customers")
regions = ibis_anim.table({"region": "string", "manager": "string"}, name="regions")
ibis_anim.declare(orders, key="order_id")
ibis_anim.declare(customers, key="customer_id")


def pipeline():
    """A long pipeline: rows made once for the sources would not survive its filters and joins."""
    paid = orders.filter((_.status == "paid") & (_.amount > 100))
    t = paid.join(customers, "customer_id").left_join(regions, "region")
    t = t.filter(_.manager == "Ann")
    t = t.filter(_.region != "B", _.amount < 400)
    t = t.mutate(big=_.amount > 200)
    t = t.filter(_.big)
    summary = t.group_by("region").aggregate(revenue=_.amount.sum(), orders=_.count())
    summary = summary.filter(_.revenue > 300)
    return summary.mutate(tier=ibis_anim.ifelse(_.revenue > 500, "high", "low")).order_by(_.revenue.desc())


def run(step, facts):
    """The inputs and the output of a step on its generated rows."""
    made = for_step(step, facts)
    mapping = {op: ibis.memtable(rows).cast(op.schema).op() for op, rows in made.items()}
    replay = lambda table: table.op().replace(mapping).to_expr().to_pyarrow()  # noqa: E731
    return made, [replay(t) for t in step.inputs], replay(step.output)


def test_each_step_has_its_own_rows():
    steps = ibis_anim.trace(pipeline)[1]
    facts = derive(steps, table_names(steps))
    for step in steps:
        made, ins, out = run(step, facts)
        assert made and all(rows.num_rows for rows in made.values()), step.op  # rows at each step, also the tenth
        if step.op == "filter":  # rows that the filter keeps and rows that it removes
            assert 0 < out.num_rows < ins[0].num_rows, step.code
        if step.op == "join":  # keys that match and keys that do not
            key = step.used[0]
            unmatched = None in out[f"{key}_right"].to_pylist() if f"{key}_right" in out.column_names else \
                set(out[key].to_pylist()) < set(ins[0][key].to_pylist())  # a left join keeps the row; an inner join drops it
            assert out.num_rows and unmatched, step.code
        if step.op.endswith("aggregate"):  # groups of more than one row
            assert out.num_rows < ins[0].num_rows, step.code


def test_rows_follow_the_facts_and_the_join_keys():
    steps = ibis_anim.trace(pipeline)[1]
    facts = derive(steps, table_names(steps))
    made, ins, out = run(steps[1], facts)  # paid.join(customers): paid comes after the filter
    paid = ins[0].to_pylist()
    assert all(r["status"] == "paid" and r["amount"] > 100 for r in paid)  # what the filter before guarantees
    keys, references = set(ins[1]["customer_id"].to_pylist()), {r["customer_id"] for r in paid}
    assert keys & references and keys - references  # the key only partly matches
    assert len(keys) == ins[1].num_rows  # a declared key: distinct values
    flagged = next(s for s in steps if s.code.endswith("filter(_.big)"))
    rows = run(flagged, facts)[1][0].to_pylist()  # big comes from a mutate before: it agrees with amount in each row
    assert all(r["big"] == (r["amount"] > 200) for r in rows) and {r["big"] for r in rows} == {True, False}


def test_a_step_that_casts_text_has_no_rows():
    steps = ibis_anim.trace(lambda: customers.mutate(code=_.code.cast("int64")))[1]
    facts = derive(steps, table_names(steps))
    made, ins, out = run(steps[0], facts)
    assert made and not any(rows.num_rows for rows in made.values()) and out.num_rows == 0
    assert any("fails on a text" in w for w in facts[steps[0].output.op()].warnings)


def test_a_step_on_real_data_gets_no_rows():
    real = ibis_anim.memtable({"x": [1, 2, 3]})
    steps = ibis_anim.trace(lambda: real.filter(_.x > 1))[1]
    assert for_step(steps[0], derive(steps, table_names(steps))) == {}


def last_input(func):
    """The rows of the first input of the last step of `func`."""
    steps = ibis_anim.trace(func)[1]
    _, ins, out = run(steps[-1], derive(steps, table_names(steps)))
    return ins[0].to_pylist(), out


def test_rows_after_a_filter_on_a_computed_column_pass_it():
    rows = last_input(lambda: orders.mutate(big=_.amount > 200).filter(_.big).mutate(x=_.amount * 2))[0]
    assert rows and all(r["big"] and r["amount"] > 200 for r in rows)
    rows = last_input(lambda: orders.mutate(x=_.amount * 2).filter(_.x > 300).mutate(y=_.x + 1))[0]
    assert rows and all(r["x"] > 300 and r["x"] == r["amount"] * 2 for r in rows)


def test_each_join_of_a_chain_has_matching_keys():
    def chain():
        big = orders.filter(_.customer_id > 100)  # its keys do not overlap the plain values 1, 2, 3 of customers
        return big.join(customers, "customer_id").join(regions, "region")

    rows, out = last_input(chain)
    assert rows and out.num_rows


def test_distinct_keeps_a_declared_key_unique():
    rows = last_input(lambda: customers.distinct())[0]
    keys = [r["customer_id"] for r in rows]
    assert len(set(keys)) == len(keys)


def test_values_near_a_bound_of_the_date_range_stay_in_the_range():
    import datetime

    from ibis_anim._samples import _around

    for dtype, bound in (("date", datetime.date.max), ("date", datetime.date.min), ("timestamp", datetime.datetime.max)):
        near = _around(ibis.dtype(dtype), bound)  # an OverflowError before the fix: bound ± 1 day is out of the range
        assert bound in near and len(set(near)) > 1, (dtype, near)
    history = ibis_anim.table({"customer_id": "int64", "valid_to": "date"}, name="history")
    end = datetime.date(9999, 12, 31)  # the open end of a history table

    def current():
        return history.filter(history.valid_to == ibis.date(end))

    _, steps = ibis_anim.trace(current)
    rows = for_step(steps[0], derive(steps, table_names(steps)))[history.op()].to_pylist()
    passing = [r for r in rows if r["valid_to"] == end]
    assert passing and len(passing) < len(rows), rows  # rows on both sides of the test


def test_a_named_column_of_an_earlier_table_reads_the_rows_of_the_input():
    from ibis_anim._scene import _run_on_samples

    def pipeline():
        label = ibis.coalesce(orders.status, ibis.literal("none"))  # a column of orders
        big = orders.filter(orders.amount > 100)
        return big.select("order_id", label=label)  # ibis reads the column on the rows of big

    _, steps = ibis_anim.trace(pipeline)
    step = steps[-1]
    assert step.op == "select" and len(step.inputs) == 2  # big, and the one-column table of label
    given = {op: (rows, False) for op, rows in for_step(step, derive(steps, table_names(steps))).items()}
    ins, out, _ = _run_on_samples(step, given)  # an IbisError before the fix: no rows for orders
    big_rows, label_rows = ins[0].rows, ins[1].rows
    assert big_rows and [r["label"] for r in label_rows] == [r["status"] or "none" for r in big_rows]  # the same rows
    assert [r["label"] for r in out.rows] == [r["label"] for r in label_rows]


def _rows_of_last_step(pipeline):
    from ibis_anim._scene import _rows_for_step

    _, steps = ibis_anim.trace(pipeline)
    facts = derive(steps, table_names(steps))
    given = {op: (rows, False) for op, rows in for_step(steps[-1], facts).items()}
    return _rows_for_step(steps[-1], given, {}, facts)


def test_a_filter_gets_rows_that_pass_it_also_for_a_narrow_range_or_a_text_test():
    def narrow():
        big = orders.filter(orders.amount > 100)
        return big.filter(big.amount < 120)  # before the fix: the values jump from 100 over 120

    def prefix():
        return orders.filter(orders.status.startswith("VIP"))  # before the fix: A, B, C never start with VIP

    for pipeline, passes in ((narrow, lambda r: 100 < r["amount"] < 120), (prefix, lambda r: r["status"].startswith("VIP"))):
        ins, out, _, _ = _rows_of_last_step(pipeline)
        assert out.rows and all(passes(r) for r in out.rows), out.rows
        assert any(not passes(r) for r in ins[0].rows if r["amount"] is not None and r["status"] is not None), ins[0].rows


def test_a_generated_result_says_whether_it_is_empty_for_any_data():
    def no_value():
        paid = orders.filter(orders.status == "a")
        return paid.filter(paid.status == "b")

    def impossible():
        return orders.filter((orders.amount > 100) & (orders.amount < 50))

    for pipeline in (no_value, impossible, lambda: orders.limit(0)):
        ins, out, rows, caption = _rows_of_last_step(pipeline)
        assert not out.rows and "for any data" in (caption or ""), (pipeline, caption)

    def constant_year():  # empty, but the facts cannot prove it: no rows found, and the caption does not say empty
        return orders.mutate(day=ibis.date("2024-01-01")).filter(_.day.year() == 2023)

    ins, out, rows, caption = _rows_of_last_step(constant_year)
    assert not out.rows and "can still have rows" in (caption or ""), caption


if __name__ == "__main__":
    test_each_step_has_its_own_rows()
    test_rows_follow_the_facts_and_the_join_keys()
    test_a_step_that_casts_text_has_no_rows()
    test_a_step_on_real_data_gets_no_rows()
    test_rows_after_a_filter_on_a_computed_column_pass_it()
    test_each_join_of_a_chain_has_matching_keys()
    test_distinct_keeps_a_declared_key_unique()
    test_values_near_a_bound_of_the_date_range_stay_in_the_range()
    test_a_named_column_of_an_earlier_table_reads_the_rows_of_the_input()
    test_a_filter_gets_rows_that_pass_it_also_for_a_narrow_range_or_a_text_test()
    test_a_generated_result_says_whether_it_is_empty_for_any_data()
    print("ok")

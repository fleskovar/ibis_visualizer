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


if __name__ == "__main__":
    test_each_step_has_its_own_rows()
    test_rows_follow_the_facts_and_the_join_keys()
    test_a_step_that_casts_text_has_no_rows()
    test_a_step_on_real_data_gets_no_rows()
    print("ok")

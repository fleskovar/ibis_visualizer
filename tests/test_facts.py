"""Checks for the abstract interpretation of steps. Run with `python tests/test_facts.py` or pytest."""

import portion as P

import ibis_anim
from ibis_anim import _
from ibis_anim._derive import derive
from ibis_anim._facts import MAYBE, NEVER, describe
from ibis_anim._scope import table_names

customers = ibis_anim.table({"customer_id": "int64", "plan": "string"}, name="customers")
orders = ibis_anim.table({"order_id": "int64", "customer_id": "int64", "amount": "float64", "status": "string"}, name="orders")
tickets = ibis_anim.table({"customer_id": "int64", "priority": "string"}, name="tickets")
ibis_anim.declare(customers, key="customer_id", values={"plan": {"pro", "basic", "trial"}})
ibis_anim.declare(orders, key="order_id", values={"status": {"paid", "refunded"}, "amount": (0, None)},
                  references={"customer_id": customers.customer_id})


def facts_of(func):
    steps = ibis_anim.trace(func)[1]
    facts = derive(steps, table_names(steps))
    return [facts[s.output.op()] for s in steps]


def pipeline():
    active = customers.filter(_.plan != "trial")
    paid = orders.filter((_.status == "paid") & (_.amount > 100))
    spend = paid.group_by("customer_id").aggregate(spend=_.amount.sum(), n=_.count())
    return active.left_join(spend, "customer_id", rname="{name}_spend").fill_null({"spend": 0.0})


def test_facts_flow_through_the_operators():
    active, paid, spend, joined, filled = facts_of(pipeline)
    assert describe(active.column("plan").values) == "{basic, pro}" and active.unique("customer_id")
    assert paid.column("amount").values == P.open(100, P.inf) and paid.column("status").nulls == NEVER
    assert spend.unique("customer_id") and spend.column("n").values == P.closed(1, P.inf)
    assert spend.column("spend").values == P.closed(0, P.inf)  # a sum of values ≥ 0
    # a left join keeps the set of the left key, and its keys when the right key is unique
    assert "active.customer_id" in joined.column("customer_id").equal_to and joined.unique("customer_id") and joined.rows == "= input"
    assert joined.column("spend").nulls == MAYBE and filled.column("spend").nulls == NEVER


def test_facts_never_come_from_data():
    plain = ibis_anim.memtable({"plan": ["pro", "basic", "trial"], "customer_id": [1, 2, 3]})  # data, but no declaration
    no_trial, = facts_of(lambda: plain.filter(_.plan != "trial"))
    right, = facts_of(lambda: orders.right_join(customers, "customer_id"))
    assert describe(no_trial.column("plan").values) == "≠ trial"  # not {basic, pro}: that would need the data
    assert frozenset({"order_id"}) not in right.keys  # customers without orders all get a null order_id


def test_warnings_point_at_mistakes():
    typo, = facts_of(lambda: orders.filter(_.status == "payed"))
    fan_out, = facts_of(lambda: customers.left_join(tickets, "customer_id"))
    filtered, ratio = facts_of(lambda: orders.filter(_.amount >= 0).mutate(share=_.amount / _.amount.sum()))
    assert typo.column("status").values.empty and "keeps no row" in typo.warnings[0]
    assert "fan-out" in fan_out.warnings[0] and fan_out.rows == "≥ input"
    assert any("can be 0" in w for w in ratio.warnings)


if __name__ == "__main__":
    test_facts_flow_through_the_operators()
    test_facts_never_come_from_data()
    test_warnings_point_at_mistakes()
    print("ok")

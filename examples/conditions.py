"""Conditions: ibis.and_ and ibis.or_ in filters, ibis.ifelse in new columns."""

import ibis_anim as ibis  # the only change from plain ibis code
from ibis_anim import _


def load():
    orders = ibis.memtable({
        "order_id": [1, 2, 3, 4, 5, 6],
        "amount": [120.0, 35.5, 250.0, 15.0, 80.0, 310.0],
        "status": ["paid", "paid", "refunded", "paid", "refunded", "paid"],
    })
    ibis.declare(orders, key="order_id")  # a schema constraint, not a value read from the data
    return orders


def big_and_paid(orders):
    return orders.filter(ibis.and_(_.status == "paid", _.amount > 50))


def needs_review(orders):
    return orders.filter(ibis.or_(_.amount > 300, _.status == "refunded"))


def labelled(orders):
    return orders.mutate(
        size=ibis.ifelse(_.amount > 100, "large", "small"),
        net=ibis.ifelse(_.status == "refunded", 0.0, _.amount),
    )


def conditions():
    orders = load()
    return big_and_paid(orders), needs_review(orders), labelled(orders)


if __name__ == "__main__":
    for table in conditions():
        print(table.to_pyarrow())

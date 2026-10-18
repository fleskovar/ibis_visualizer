"""A chained statement: join, mutate and aggregate in one expression; then filter, order_by and limit."""

import ibis_anim as ibis
from ibis_anim import _

items = ibis.memtable({"order_id": [100, 100, 101, 102], "sku": ["A", "B", "A", "C"], "qty": [2, 1, 5, 3]})
products = ibis.memtable({"sku": ["A", "B", "C"], "price": [10.0, 99.0, 4.5]})
ibis.declare(products, key="sku")

revenue = (
    items.join(products, "sku")
    .mutate(total=_.qty * _.price)
    .group_by("order_id")
    .aggregate(revenue=_.total.sum())
)
top = revenue.filter(_.revenue > 20).order_by(_.revenue.desc()).limit(1)

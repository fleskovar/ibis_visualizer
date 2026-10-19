"""A pipeline written against schemas only: there is no data at all.

The tables are `ibis.table(schema, name=...)`. To render the video, ibis_anim generates a few rows
for each step, from the facts of its inputs and from its arguments: join keys that only partly match,
rows on both sides of each test of a filter, distinct values for keys.
"""

import ibis_anim as ibis  # the only change from plain ibis code
from ibis_anim import _

orders = ibis.table({"order_id": "int64", "customer_id": "int64", "amount": "float64", "status": "string"}, name="orders")
customers = ibis.table({"customer_id": "int64", "name": "string", "region": "string"}, name="customers")
regions = ibis.table({"region": "string", "manager": "string"}, name="regions")

# schema constraints: keys only, no values
ibis.declare(orders, key="order_id")
ibis.declare(customers, key="customer_id")
ibis.declare(regions, key="region")


def paid_orders():
    return orders.filter((_.status == "paid") & (_.amount > 100))


def with_customers(paid):
    return paid.join(customers, "customer_id")


def with_managers(enriched):
    return enriched.left_join(regions, "region")


def revenue_by_region(table):
    summary = table.group_by("region").aggregate(revenue=_.amount.sum(), orders=_.count())
    return summary.mutate(tier=ibis.ifelse(_.revenue > 500, "high", "low")).order_by(_.revenue.desc())


def schema_only():
    return revenue_by_region(with_managers(with_customers(paid_orders())))


if __name__ == "__main__":
    print(ibis.report(schema_only))  # the facts need no data either
    schema_only()  # traced; the video renders at exit, from generated rows

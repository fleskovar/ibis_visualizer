"""Customer health report: a pipeline close to a real analytics job.

Five raw tables. Order revenue comes from one chain of join, mutate and aggregate. One function
cleans three tables. Spend and support load go per customer, then two left joins put everything
on the customer table. Nulls are filled, a loop adds share columns, a score and a segment are
computed, and the best account of each region is ranked.
"""

import ibis_anim as ibis  # the only change from plain ibis code
import ibis_anim.selectors as s
from ibis_anim import _

REPORT_DATE = "2024-06-30"


def load():
    customers = ibis.memtable({
        "customer_id": [1, 2, 3, 4, 5, 6],
        "name": ["Acme", "Globex", "Initech", "Umbrella", "Hooli", "Stark"],
        "region": ["EU", "US", "EU", "APAC", "US", "EU"],
        "plan": ["pro", "basic", "pro", "basic", "pro", "trial"],
    })
    orders = ibis.memtable({
        "order_id": [100, 101, 102, 103, 104, 105, 106],
        "customer_id": [1, 2, 1, 3, 5, 2, 5],
        "order_date": ["2024-04-03", "2024-04-17", "2024-05-02", "2024-05-20", "2024-06-01", "2024-06-11", "2024-06-25"],
        "status": ["shipped", "shipped", "cancelled", "shipped", "shipped", "shipped", "returned"],
    })
    items = ibis.memtable({
        "order_id": [100, 100, 101, 103, 104, 105],
        "sku": ["A1", "B2", "A1", "C3", "B2", "C3"],
        "qty": [2, 1, 5, 3, 4, 1],
    })
    products = ibis.memtable({
        "sku": ["A1", "B2", "C3"],
        "unit_price": [19.5, 120.0, 45.0],
    })
    tickets = ibis.memtable({
        "ticket_id": [1, 2, 3, 4, 5],
        "customer_id": [2, 2, 4, 5, 1],
        "priority": ["high", "low", "high", "low", "low"],
    })
    # schema constraints only (keys and foreign keys): the facts never come from the data itself
    ibis.declare(customers, key="customer_id")
    ibis.declare(orders, key="order_id", references={"customer_id": customers.customer_id})
    ibis.declare(products, key="sku")
    ibis.declare(items, references={"order_id": orders.order_id, "sku": products.sku})
    ibis.declare(tickets, key="ticket_id", references={"customer_id": customers.customer_id})
    return customers, orders, items, products, tickets


def order_revenue(items, products):
    # one chain: four steps in one statement
    return (
        items.join(products, "sku")
        .mutate(line_total=_.qty * _.unit_price)
        .group_by("order_id")
        .aggregate(revenue=_.line_total.sum(), units=_.qty.sum())
    )


def clean(customers, orders, tickets, include_returns: bool):
    # three tables change in the same function
    customers = customers.filter(_.plan != "trial")
    orders = orders.mutate(order_date=_.order_date.cast("date"))
    if include_returns:
        orders = orders.filter(_.status != "cancelled")
    else:
        orders = orders.filter(_.status == "shipped")
    tickets = tickets.mutate(urgent=(_.priority == "high").cast("int64"))
    return customers, orders, tickets


def spend_per_customer(orders, revenue):
    return orders.join(revenue, "order_id").group_by("customer_id").aggregate(
        spend=_.revenue.sum(),
        orders=_.order_id.count(),
        last_order=_.order_date.max(),
    )


def support_per_customer(tickets):
    return tickets.group_by("customer_id").aggregate(tickets=_.count(), urgent=_.urgent.sum())


def build_health(customers, spend, support):
    health = (
        customers.left_join(spend, "customer_id", rname="{name}_spend")
        .left_join(support, "customer_id", rname="{name}_support")
        .drop(s.startswith("customer_id_"))
        .fill_null({"spend": 0.0, "orders": 0, "tickets": 0, "urgent": 0})
    )
    for column in ("spend", "tickets"):  # a loop: one mutate per pass
        health = health.mutate(**{f"{column}_share": (health[column] / health[column].sum()).round(2)})
    health = health.mutate(score=(_.spend / 100 - _.urgent * 2).round(1))
    return health.mutate(
        segment=ibis.cases((_.score >= 3, "healthy"), (_.score >= 0, "watch"), else_="at risk")
    )


def rank_accounts(health):
    ranked = health.mutate(region_rank=ibis.rank().over(group_by="region", order_by=ibis.desc("score")) + 1)
    return ranked.filter(_.region_rank == 1).order_by(_.score.desc()).select("region", "name", "score", "segment")


def customer_health(include_returns: bool = False):
    customers, orders, items, products, tickets = load()
    revenue = order_revenue(items, products)
    customers, orders, tickets = clean(customers, orders, tickets, include_returns)
    spend = spend_per_customer(orders, revenue)
    support = support_per_customer(tickets)
    return rank_accounts(build_health(customers, spend, support))


if __name__ == "__main__":
    print(customer_health().to_pyarrow())

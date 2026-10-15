"""Filter, mutate, join and aggregate, spread over nested functions and one branch."""

import ibis_anim as ibis  # the only change from plain ibis code
from ibis_anim import _


def load():
    orders = ibis.memtable({
        "order_id": [101, 102, 103, 104, 105, 106, 107, 108],
        "customer_id": [1, 2, 1, 3, 4, 2, 3, 1],
        "amount": [120.0, 35.5, 80.0, 250.0, 15.0, 99.9, 42.0, 300.0],
        "status": ["paid", "paid", "refunded", "paid", "paid", "refunded", "paid", "paid"],
    })
    customers = ibis.memtable({
        "customer_id": [1, 2, 3, 4],
        "name": ["Ada", "Linus", "Grace", "Guido"],
        "region": ["EU", "EU", "US", "APAC"],
    })
    return orders, customers


def clean(orders, keep_refunds: bool):
    if not keep_refunds:
        orders = orders.filter(orders.status == "paid")
    return orders.mutate(amount_eur=(_.amount * 0.92).round(2))


def enrich(orders, customers):
    return orders.join(customers, "customer_id")


def summarize(enriched):
    return enriched.group_by("region").aggregate(revenue=_.amount_eur.sum(), orders=_.count())


def pipeline(keep_refunds: bool = False):
    orders, customers = load()
    return summarize(enrich(clean(orders, keep_refunds), customers))


if __name__ == "__main__":
    print(pipeline().to_pyarrow())

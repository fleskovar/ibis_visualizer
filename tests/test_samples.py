"""Checks for the sample rows of tables that have a schema only. Run with `python tests/test_samples.py` or pytest."""

import ibis_anim
from ibis_anim import _
from ibis_anim._derive import derive
from ibis_anim._samples import bind, generate
from ibis_anim._scope import table_names

orders = ibis_anim.table({"order_id": "int64", "customer_id": "int64", "amount": "float64", "status": "string"}, name="orders")
customers = ibis_anim.table({"customer_id": "int64", "region": "string"}, name="customers")
regions = ibis_anim.table({"region": "string", "manager": "string"}, name="regions")
ibis_anim.declare(orders, key="order_id")
ibis_anim.declare(customers, key="customer_id")


def pipeline():
    paid = orders.filter((_.status == "paid") & (_.amount > 100))
    return paid.join(customers, "customer_id").left_join(regions, "region")


def test_rows_follow_the_types_and_the_code():
    steps = ibis_anim.trace(pipeline)[1]
    names = table_names(steps)
    data = generate(steps, names, derive(steps, names))
    column = lambda table, name: data[table.op()][name].to_pylist()  # noqa: E731
    assert len(set(column(customers, "customer_id"))) == 6  # a declared key: distinct values
    keys, references = set(column(customers, "customer_id")), set(column(orders, "customer_id"))
    assert keys & references and references - keys  # a join key only partly matches
    regions_a, regions_b = set(column(customers, "region")), set(column(regions, "region"))
    assert regions_a & regions_b and regions_a ^ regions_b  # also for strings: A, B, C against A, D, C
    status, amount = column(orders, "status"), column(orders, "amount")
    assert "paid" in status and set(status) - {"paid"}  # rows on both sides of status == "paid"
    assert any(a > 100 for a in amount) and any(a <= 100 for a in amount)
    assert bind(data)(pipeline()).to_pyarrow().num_rows > 0  # the pipeline runs on the rows


if __name__ == "__main__":
    test_rows_follow_the_types_and_the_code()
    print("ok")

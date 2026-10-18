from dbt_ibis import depends_on, ref


@depends_on(ref("stg_orders"), ref("usa_stores"), ref("stg_customers"))
def model(orders, stores, customers):
    in_usa = orders.join(stores, "store_id")
    with_customers = in_usa.join(customers, "customer_id")
    return with_customers.group_by(["store_name", "customer_country"]).aggregate(
        revenue=with_customers["amount"].sum(),
        orders=with_customers["order_id"].count(),
    )

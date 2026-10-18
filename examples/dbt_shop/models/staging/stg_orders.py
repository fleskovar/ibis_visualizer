from dbt_ibis import depends_on, source


@depends_on(source("raw", "orders"))
def model(orders):
    shipped = orders.filter(orders["status"] == "shipped")
    return shipped.mutate(store_id=shipped["store_id"].cast("int"))

from dbt_ibis import depends_on, source


@depends_on(source("raw", "stores"))
def model(stores):
    return stores.mutate(store_id=stores["store_id"].cast("int"))

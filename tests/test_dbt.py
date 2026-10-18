"""Checks for dbt-ibis models: the references run upstream first. Run with `python tests/test_dbt.py` or pytest."""

from pathlib import Path

import ibis_anim
from ibis_anim._derive import derive
from ibis_anim._samples import generate
from ibis_anim._scope import table_names

PROJECT = Path(__file__).parent.parent / "examples" / "dbt_shop"


def test_a_model_runs_with_the_models_upstream_of_it():
    model = ibis_anim.load_dbt_model(PROJECT / "models" / "marts" / "store_revenue.py")
    result, steps = ibis_anim.trace(model)
    order = list(dict.fromkeys(s.function for s in steps))
    assert order == ["stg_orders", "stg_stores", "usa_stores", "store_revenue"]  # each model has its own name
    assert model.__name__ == "store_revenue" and result.columns == ("store_name", "customer_country", "revenue", "orders")
    names = table_names(steps)
    facts = derive(steps, names)
    assert not any(facts[s.output.op()].warnings for s in steps)  # the dbt tests declare the keys: no fan-out
    assert facts[steps[3].output.op()].unique("store_id")  # a test of the model stg_stores, kept after the filter
    stores = next(rows for op, rows in generate(steps, names, facts).items() if op.name == "stores")
    assert all(v.isdigit() for v in stores["store_id"].to_pylist())  # text that the model casts to int


def test_the_command_line_finds_the_function():
    import sys

    import ibis_anim.__main__ as cli

    videos, argv, render = [], sys.argv, ibis_anim._render_at_exit
    ibis_anim._render_at_exit = lambda steps, name=None: videos.append((name, len(steps)))
    try:
        cli.main([str(PROJECT / "models" / "marts" / "store_revenue.py")])  # the dbt model of the file
        cli.main([f"{PROJECT.parent / 'schema_only.py'}:paid_orders"])  # an absolute path: on Windows, also C:
    finally:
        ibis_anim._render_at_exit, sys.argv = render, argv
    assert videos == [("store_revenue", 7), ("paid_orders", 1)]


if __name__ == "__main__":
    test_a_model_runs_with_the_models_upstream_of_it()
    test_the_command_line_finds_the_function()
    print("ok")

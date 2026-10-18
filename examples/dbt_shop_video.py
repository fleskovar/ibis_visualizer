"""Visualize a dbt-ibis model and every model upstream of it.

The model is a plain dbt-ibis `model` function with `@depends_on(...)`. ibis_anim follows its references
through the project in `dbt_shop/`: the ibis models upstream run first, and the sources and the SQL model
become tables with a schema only (from the `data_type` in the YAML), with generated rows for the video.
"""

from pathlib import Path

import ibis_anim

model = ibis_anim.load_dbt_model(Path(__file__).parent / "dbt_shop" / "models" / "marts" / "store_revenue.py")

if __name__ == "__main__":
    print(ibis_anim.report(model))
    _, steps = ibis_anim.trace(model)  # the model and every model upstream of it
    print(ibis_anim.render(steps, "dbt_shop"))  # the video: media/dbt_shop.mp4

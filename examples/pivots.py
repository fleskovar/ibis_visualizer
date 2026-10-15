"""Pivot a long table to wide, compute a total, and pivot it back to long."""

import ibis_anim as ibis  # the only change from plain ibis code
import ibis_anim.selectors as s


def monthly_sales():
    return ibis.memtable({
        "store": ["north", "north", "north", "south", "south", "south"],
        "month": ["jan", "feb", "mar", "jan", "feb", "mar"],
        "units": [10, 14, 9, 7, 12, 20],
    })


def to_wide(sales):
    wide = sales.pivot_wider(names_from="month", values_from="units")
    return wide.mutate(total=wide.jan + wide.feb + wide.mar)


def to_long(wide):
    return wide.pivot_longer(s.cols("jan", "feb", "mar"), names_to="month", values_to="units")


def pivots():
    return to_long(to_wide(monthly_sales()))


if __name__ == "__main__":
    print(pivots().to_pyarrow())

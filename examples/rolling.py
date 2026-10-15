"""Rolling and cumulative sums over a window, then a sort and a limit."""

import ibis_anim as ibis  # the only change from plain ibis code
from ibis_anim import _


def prices():
    return ibis.memtable({
        "ticker": ["INIT", "ACME", "ACME", "INIT", "ACME", "INIT", "ACME", "INIT", "ACME", "INIT"],
        "day": [3, 2, 5, 1, 1, 5, 4, 2, 3, 4],
        "volume": [10, 20, 50, 5, 10, 20, 40, 5, 30, 10],
    })


def add_rolling(t):
    last_3_days = ibis.window(group_by="ticker", order_by="day", preceding=2, following=0)
    return t.mutate(volume_3d=_.volume.sum().over(last_3_days))


def add_running_total(t):
    # `.sum().over(group_by=..., order_by=...)` sums the whole partition in ibis; cumsum stops at the current row.
    return t.mutate(volume_total=_.volume.cumsum(group_by="ticker", order_by="day"))


def busiest_days(t):
    return t.order_by(_.volume_3d.desc()).limit(3)


def rolling():
    return busiest_days(add_running_total(add_rolling(prices())))


if __name__ == "__main__":
    print(rolling().to_pyarrow())

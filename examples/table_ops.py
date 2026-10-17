"""Set operations, nulls, casts, columns built outside the table, select, rename and unnest."""

import ibis_anim as ibis  # the only change from plain ibis code


def load():
    monday = ibis.memtable({
        "player": ["ann", "bob", "cid", "ann"],
        "nick": ["A", None, "Cee", "A"],
        "score": [12, None, 7, 12],
        "tags": [["pro", "eu"], ["new"], [], ["pro", "eu"]],
    })
    tuesday = ibis.memtable({
        "player": ["dee", "bob", "ann"],
        "nick": ["Dee", None, "A"],
        "score": [9, None, 15],  # ann played again with another score: the same player, but not the same row
        "tags": [["us"], ["new"], ["pro", "eu"]],
    })
    return monday, tuesday


def combine(monday, tuesday):
    both = monday.union(tuesday)
    return both.distinct()


def clean(players):
    players = players.fill_null({"score": 0})
    players = players.cast({"score": "int64"})
    label = ibis.coalesce(players.nick, players.player)  # columns built outside the table call
    points = (players.score * 10).cast("int64")
    return players.mutate(label=label, points=points)


def reshape(players):
    slim = players.select("label", "points", "tags")
    slim = slim.rename(name="label")
    return slim.unnest("tags")


def table_ops():
    monday, tuesday = load()
    regulars = monday.intersect(tuesday)
    return reshape(clean(combine(monday, tuesday))), regulars


if __name__ == "__main__":
    tags, regulars = table_ops()
    print(tags.to_pyarrow(), regulars.to_pyarrow())

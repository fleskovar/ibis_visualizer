"""Checks for conditions: the structure of and_ / or_ filters, and their value on each row. Run with python or pytest."""

import ibis_anim
from ibis_anim import _
from ibis_anim._scene import _condition_tree, _evaluate, _leaves, _tree_text

orders = ibis_anim.memtable({"amount": [120.0, None, 20.0], "status": ["paid", "paid", "refunded"]})


def test_condition_structure():
    both = orders.filter(ibis_anim.and_(_.status == "paid", _.amount > 50)).op()
    either = orders.filter(ibis_anim.or_(_.amount > 100, _.status == "refunded")).op()
    mixed = orders.filter((_.status == "paid") & ((_.amount > 100) | (_.amount < 10))).op()
    assert _condition_tree(both)[0] == "and" and len(_leaves(_condition_tree(both))) == 2
    assert _condition_tree(either)[0] == "or"
    tree = _condition_tree(mixed)
    assert _tree_text(tree, {id(leaf): j + 1 for j, leaf in enumerate(_leaves(tree))}) == "1 AND (2 OR 3)"


def test_each_row_gets_the_value_of_each_test():
    tree = _condition_tree(orders.filter(ibis_anim.and_(_.status == "paid", _.amount > 50)).op())
    values = _evaluate(_leaves(tree), orders, orders.to_pyarrow().to_pylist())
    assert values == [[True, True], [True, None], [False, False]]  # a null amount gives an unknown test


def test_ifelse_facts():
    from ibis_anim._derive import column_text, derive
    from ibis_anim._scope import table_names

    steps = ibis_anim.trace(lambda: orders.mutate(size=ibis_anim.ifelse(_.amount > 100, "large", "small")))[1]
    names = table_names(steps)
    facts = derive(steps, names)[steps[0].output.op()]
    assert column_text(facts, "size", names[steps[0].output.op()]) == "{large, small} · not null"


if __name__ == "__main__":
    test_condition_structure()
    test_each_row_gets_the_value_of_each_test()
    test_ifelse_facts()
    print("ok")

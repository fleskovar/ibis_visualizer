"""Checks for dbt-ibis models: the references run upstream first. Run with `python tests/test_dbt.py` or pytest."""

from pathlib import Path

import portion as P

import ibis_anim
from ibis_anim._derive import derive
from ibis_anim._samples import for_step
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
    warnings = [w for s in steps for w in facts[s.output.op()].warnings]
    assert not any("fan-out" in w for w in warnings)  # the dbt tests declare the keys: no fan-out
    assert facts[steps[3].output.op()].unique("store_id")  # a test of the model stg_stores, kept after the filter
    cast = next(s for s in steps if s.function == "stg_stores")  # text that the model casts to int: the types only
    assert not any(rows.num_rows for rows in for_step(cast, facts).values())
    assert any("fails on a text" in w for w in facts[cast.output.op()].warnings)


def test_each_model_hands_its_table_to_the_models_that_read_it():
    from ibis_anim._scope import calls

    _, steps = ibis_anim.trace(ibis_anim.load_dbt_model(PROJECT / "models" / "marts" / "store_revenue.py"))
    names = table_names(steps)
    flow = [(c.name, [names[op] for op in c.reads], [names[op] for op in c.hands]) for c in calls(steps)]
    assert flow == [
        ("stg_orders()", ["orders"], ["stg_orders"]),
        ("stg_stores()", ["stores"], ["stg_stores"]),
        ("usa_stores()", ["stg_stores"], ["usa_stores"]),  # the table of stg_stores() is the argument of usa_stores()
        ("store_revenue()", ["stg_orders", "usa_stores", "customers"], ["store_revenue"]),
    ]


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


def test_a_model_video_has_its_own_steps_and_the_facts_of_the_models_upstream():
    assert "model_steps" in ibis_anim.__all__  # a public function: the documented way to render a part of a pipeline
    name, by_model, facts = ibis_anim.model_steps(PROJECT / "models" / "marts" / "store_revenue.py")
    assert name == "store_revenue" and list(by_model) == ["stg_orders", "stg_stores", "usa_stores", "store_revenue"]
    own = by_model[name]
    assert [s.op for s in own] == ["join", "join", "group_by → aggregate"]
    usa_stores = own[0].inputs[1].op()  # a source of this video; the filter of usa_stores() made it
    assert facts[usa_stores].column("country").values == P.singleton("USA")  # not ANY, as for a schema from the YAML
    assert facts[usa_stores].unique("store_id")


def test_the_dbt_command_renders_one_model():
    import os
    import sys

    import ibis_anim.__main__ as cli

    videos, argv, render, cwd = [], sys.argv, ibis_anim._render_at_exit, os.getcwd()
    ibis_anim._render_at_exit = lambda steps, name=None, facts=None: videos.append((name, len(steps), facts is not None))
    try:
        cli.main(["dbt", str(PROJECT / "models" / "marts" / "store_revenue.py")])
        os.chdir(PROJECT / "models")  # a model name: the project is the dbt_project.yml above the current folder
        cli.main(["dbt", "usa_stores"])
    finally:
        ibis_anim._render_at_exit, sys.argv = render, argv
        os.chdir(cwd)
    assert videos == [("store_revenue", 3, True), ("usa_stores", 1, True)]


def test_a_video_renders_again_only_when_its_inputs_change():
    import os
    import shutil
    import tempfile

    from ibis_anim._dbt import _trace_models, render_models, video_key

    def key(project, model):
        _, by_model, _, found = _trace_models(project / "models" / model)
        return video_key(by_model, found)

    renders, render, cwd = [], ibis_anim._render_at_exit, os.getcwd()

    def fake(steps, name=None, facts=None):  # a video file, without manim
        renders.append(name)
        Path("media").mkdir(exist_ok=True)
        Path("media", f"{name}.mp4").write_bytes(b"")
        return Path("media", f"{name}.mp4")

    ibis_anim._render_at_exit = fake
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "dbt_shop"
        shutil.copytree(PROJECT, project, ignore=shutil.ignore_patterns("__pycache__"))
        os.chdir(tmp)
        try:
            usa, revenue = key(project, "staging/usa_stores.py"), key(project, "marts/store_revenue.py")
            with open(project / "models" / "marts" / "store_revenue.py", "a") as f:
                f.write("# a change\n")
            assert key(project, "staging/usa_stores.py") == usa  # a change downstream of the model
            assert key(project, "marts/store_revenue.py") != revenue
            model = project / "models" / "staging" / "usa_stores.py"
            render_models(model)
            render_models(model)  # up to date
            render_models(model, force=True)
            later = Path("media", "keys", "usa_stores.sha256").stat().st_mtime + 10
            os.utime(Path("media", "usa_stores.mp4"), (later, later))  # another render wrote the video, e.g. the whole tree
            render_models(model)
        finally:
            ibis_anim._render_at_exit = render
            os.chdir(cwd)
    assert renders == ["usa_stores"] * 3


if __name__ == "__main__":
    test_a_model_runs_with_the_models_upstream_of_it()
    test_each_model_hands_its_table_to_the_models_that_read_it()
    test_the_command_line_finds_the_function()
    test_a_model_video_has_its_own_steps_and_the_facts_of_the_models_upstream()
    test_the_dbt_command_renders_one_model()
    test_a_video_renders_again_only_when_its_inputs_change()
    print("ok")

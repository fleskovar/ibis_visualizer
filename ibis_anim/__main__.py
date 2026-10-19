"""Visualize a script, a function or a dbt-ibis model, with no change to its code.

    python -m ibis_anim FILE[:FUNCTION] [ARGS...]
    python -m ibis_anim dbt MODEL [--deps] [--jobs N] [--force]

With FUNCTION, that function runs under the tracer, and the video has its name. A dbt-ibis model gets its
inputs from its project. Without FUNCTION, the file runs as a script with ARGS, and the video has the name
of the file. When the file defines a dbt-ibis `model`, that model runs. The file can use `import ibis`.

The video of FILE shows a dbt-ibis model with each model upstream of it. The `dbt` command shows the steps of the
model only, which is faster: the models upstream of it run without a video. With --deps, each upstream ibis model
gets its own video too. A video whose inputs did not change since its last render stays, unless --force.
"""

import argparse
import runpy
import sys
from pathlib import Path

import ibis_anim
from ibis_anim._dbt import is_model, model_file, render_models


def main(argv: list[str]):
    if not argv:
        sys.exit(__doc__)
    if argv[0] == "dbt":
        return dbt(argv[1:])
    file, _, name = argv[0].rpartition(":")
    if not name.isidentifier():  # no FUNCTION, or the drive of a Windows path (C:\models\a.py)
        file, name = argv[0], None
    sys.argv = [file, *argv[1:]]
    sys.path.insert(0, str(Path(file).resolve().parent))  # as `python FILE`: the file can import the files next to it
    if name:
        with ibis_anim.recording(None):  # the steps at the top of the file are not part of the function
            func = runpy.run_path(file).get(name)
        if not callable(func):
            sys.exit(f"ibis_anim: {file} has no function {name}")
    else:
        module = runpy.run_path(file, run_name="__main__")  # a plain script: its steps render at exit
        func = module.get("model") or module.get("test")
        if not is_model(func):
            return
    _, steps = ibis_anim.trace(func)
    ibis_anim._render_at_exit(steps, func.__name__)


def dbt(argv: list[str]):
    parser = argparse.ArgumentParser(prog="python -m ibis_anim dbt", description="A video of the steps of one dbt-ibis model.")
    parser.add_argument("model", help="the file of the model, or its name in the dbt project of the current folder")
    parser.add_argument("--deps", action="store_true", help="also a video for each ibis model upstream of it")
    parser.add_argument("--jobs", type=int, help="with --deps, the number of videos that render at the same time (default: the number of CPUs)")
    parser.add_argument("--force", action="store_true", help="render each video again, also when its inputs did not change")
    args = parser.parse_args(argv)
    failed = render_models(model_file(args.model), args.deps, args.jobs, args.force)
    if failed:
        sys.exit(f"ibis_anim: no video for {', '.join(failed)}")


if __name__ == "__main__":
    main(sys.argv[1:])

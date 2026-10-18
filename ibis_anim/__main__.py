"""Visualize a script, a function or a dbt-ibis model, with no change to its code.

    python -m ibis_anim FILE[:FUNCTION] [ARGS...]

With FUNCTION, that function runs under the tracer, and the video has its name. A dbt-ibis model gets its
inputs from its project. Without FUNCTION, the file runs as a script with ARGS, and the video has the name
of the file. When the file defines a dbt-ibis `model`, that model runs. The file can use `import ibis`.
"""

import runpy
import sys
from pathlib import Path

import ibis_anim
from ibis_anim._dbt import is_model


def main(argv: list[str]):
    if not argv:
        sys.exit(__doc__)
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


if __name__ == "__main__":
    main(sys.argv[1:])

"""dbt-ibis models: run a model, and every model upstream of it, under the tracer.

A dbt-ibis model is a `model` function with `@depends_on(ref("..."), source("...", "..."))`. To run it,
each reference becomes a table:

- `ref("name")` to an ibis model (`name.ibis` or `name.py`): that model runs first, with its own inputs
- `ref("name")` to a SQL model, or `source("src", "table")`: a table with a schema only, from the
  `data_type` of its columns in the YAML files of the project (as dbt-ibis requires)

The project is the nearest folder with `dbt_project.yml` above the file of the model.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import inspect
import re
from pathlib import Path

import ibis
import sqlglot
import yaml
from ibis.backends.sql.datatypes import DuckDBType
from sqlglot import exp

from ibis_anim._facts import declare
from ibis_anim._scope import name_table

MODEL_SUFFIXES = (".ibis", ".py")


def is_model(func) -> bool:
    return getattr(func, "depends_on", None) is not None


def run(func):
    """Call a dbt-ibis model with its inputs; the models upstream of it run first."""
    project = Project.of(func)
    name = project.name(func)
    return name_table(project._declare(("ref", name), project.call(func)), name)  # the tests of the model itself


def load_model(path, name: str | None = None):
    """The `model` function of a dbt-ibis file (`.ibis` or `.py`)."""
    path = Path(path)
    name = name or path.stem
    loader = importlib.machinery.SourceFileLoader(f"dbt_model_{name}", str(path))  # also reads .ibis files
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    func = getattr(module, "model", None) or getattr(module, "test")
    _rename(func, name)
    return func


def _rename(func, name: str):
    """Give the function the name of its model, so that the trace says `stg_stores()`, not `model()`."""
    inner = inspect.unwrap(func)
    if inner.__code__.co_name in ("model", "test"):
        inner.__code__ = inner.__code__.replace(co_name=name, co_qualname=name)
        for f in {inner, func}:  # also the name of the video
            f.__name__ = f.__qualname__ = name


def _dtype(text: str):
    """An ibis type from a dbt `data_type` (SQL names like varchar, bigint, decimal(10, 2))."""
    for parse in (DuckDBType.from_string, ibis.dtype):
        try:
            return parse(text)
        except Exception:
            continue
    return ibis.dtype("string")


class Project:
    def __init__(self, root: Path):
        self.root, self.files, self.schemas, self.tables, self.tests = root, {}, {}, {}, {}
        config_file = root / "dbt_project.yml"
        config = yaml.safe_load(config_file.read_text()) if config_file.exists() else {}
        folders = [root / p for p in (config or {}).get("model-paths", ["models"])] + [root / p for p in (config or {}).get("seed-paths", ["seeds"])]
        for folder in [f for f in folders if f.exists()] or [root]:
            for path in folder.rglob("*"):
                if "__ibis_sql" in path.parts:  # the SQL that dbt-ibis generates
                    continue
                if path.suffix in (*MODEL_SUFFIXES, ".sql", ".csv"):
                    self.files.setdefault(path.stem, path)
                if path.suffix in (".yml", ".yaml"):
                    self._read_yaml(path)

    @classmethod
    def of(cls, func) -> Project:
        path = Path(inspect.getsourcefile(inspect.unwrap(func))).resolve()
        root = next((p for p in path.parents if (p / "dbt_project.yml").exists()), path.parent)
        return cls(root)

    def _read_yaml(self, path: Path):
        doc = yaml.safe_load(path.read_text()) or {}
        for kind in ("models", "seeds", "snapshots"):
            for node in doc.get(kind) or []:
                self.schemas[("ref", node["name"])] = self._columns(node)
                self.tests[("ref", node["name"])] = self._tests(node)
        for src in doc.get("sources") or []:
            for table in src.get("tables") or []:
                self.schemas[("source", src["name"], table["name"])] = self._columns(table)
                self.tests[("source", src["name"], table["name"])] = self._tests(table)

    @staticmethod
    def _columns(node) -> dict:
        return {c["name"]: _dtype(c["data_type"]) for c in node.get("columns") or [] if c.get("data_type")}

    @staticmethod
    def _tests(node) -> dict:
        """dbt tests and constraints of the columns: unique, not_null, accepted_values. dbt checks them at
        each build, so they are facts about the table."""
        unique, not_null, values = [], [], {}
        for column in node.get("columns") or []:
            checks = [*(column.get("tests") or []), *(column.get("data_tests") or [])]
            checks += [c.get("type") for c in column.get("constraints") or []]
            for check in checks:
                kind, args = (check, {}) if isinstance(check, str) else next(iter(check.items()))
                if kind in ("unique", "primary_key"):
                    unique.append(column["name"])
                if kind in ("not_null", "primary_key"):
                    not_null.append(column["name"])
                if kind == "accepted_values" and (args or {}).get("values"):
                    values[column["name"]] = set(args["values"])
        return {"unique": unique, "not_null": not_null, "values": values}

    def _declare(self, key, table):
        tests = self.tests.get(key)
        if tests and any(tests.values()):
            declare(table, **tests)
        return table

    def name(self, func) -> str:
        path = Path(inspect.getsourcefile(inspect.unwrap(func)))
        _rename(func, path.stem)
        return path.stem

    def call(self, func):
        return func(*[self.table(reference) for reference in func.depends_on])

    def table(self, reference):
        """The table of a `ref` or a `source`: a model that runs, or a table with a schema only."""
        if hasattr(reference, "source_name"):
            key = ("source", reference.source_name, reference.table_name)
            if key not in self.tables:
                self.tables[key] = self._schema_table(key, reference.table_name)
            return self.tables[key]
        name = reference.name
        if ("ref", name) not in self.tables:
            path = self.files.get(name)
            if path is not None and path.suffix in MODEL_SUFFIXES:  # an ibis model: run it, with its own inputs first
                self.tables[("ref", name)] = name_table(self._declare(("ref", name), self.call(load_model(path, name))), name)
            else:  # a SQL model or a seed
                self.tables[("ref", name)] = self._schema_table(("ref", name), name, path)
        return self.tables[("ref", name)]

    def _schema_table(self, key, name: str, sql_file: Path | None = None):
        schema = self.schemas.get(key) or (_sql_columns(sql_file) if sql_file is not None and sql_file.suffix == ".sql" else {})
        if not schema:
            what = f"source {key[1]}.{key[2]}" if key[0] == "source" else f"model {name}"
            raise ValueError(f"no columns for the {what}: declare its columns with a data_type in a YAML file of the project")
        return self._declare(key, ibis.table(schema, name=name))


def _sql_columns(path: Path) -> dict:
    """Columns of a simple SELECT model when the YAML has none: the names of its projections, the type of
    a CAST when there is one, else string."""
    sql = re.sub(r"\{\{.*?\}\}", "dbt_relation", path.read_text())  # {{ ref(...) }} and {{ source(...) }}
    sql = re.sub(r"\{%.*?%\}", "", sql, flags=re.S)
    try:
        select = sqlglot.parse_one(sql)
    except Exception:
        return {}
    columns = {}
    for projection in select.expressions if isinstance(select, exp.Select) else []:
        if isinstance(projection, exp.Star) or not projection.alias_or_name:
            return {}  # SELECT * needs the YAML
        value = projection.unalias()
        columns[projection.alias_or_name] = _dtype(value.to.sql()) if isinstance(value, exp.Cast) else ibis.dtype("string")
    return columns

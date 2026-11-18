"""The routine suite must retain every pair and an accessible exhaustive run."""

from itertools import combinations, product
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from conftest import _pairwise_items


def pairs(items):
    result = set()
    for item in items:
        values = tuple(sorted(item.callspec.params.items()))
        result.update(combinations(values, 2) if len(values) > 1 else [values])
    return result


@pytest.mark.parametrize("dimensions", [(12, 4, 3), (12, 2, 2), (3, 3, 3, 2), (2, 3), (3,)])
def test_routine_matrix_covers_every_pair_of_parameter_values(dimensions):
    items = [SimpleNamespace(callspec=SimpleNamespace(params={
        f"axis_{index}": value for index, value in enumerate(values)
    })) for values in product(*(range(size) for size in dimensions))]
    selected = _pairwise_items(items)
    assert pairs(selected) == pairs(items)
    assert [id(item) for item in selected] == [id(item) for item in _pairwise_items(items)]
    if len(dimensions) > 2:
        assert len(selected) < len(items)
    else:
        assert len(selected) == len(items)


def test_cli_exhaustive_flag_retains_all_cases_and_explicit_matrix_selections(tmp_path):
    # Collect in a subprocess so this nested session cannot alter the real Qt app.
    (tmp_path / "conftest.py").write_text(
        Path(__file__).with_name("conftest.py").read_text(encoding="utf-8"), encoding="utf-8",
    )
    (tmp_path / "pytest.ini").write_text(
        Path(__file__).resolve().parents[1].joinpath("pytest.ini").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "test_sample.py").write_text('''import pytest

@pytest.mark.render_matrix
@pytest.mark.parametrize("a", range(3))
@pytest.mark.parametrize("b", range(2))
@pytest.mark.parametrize("c", range(2))
def test_matrix(a, b, c):
    pass

@pytest.mark.exhaustive
def test_expensive():
    pass
''', encoding="utf-8")

    def collect(*args):
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
            cwd=tmp_path, capture_output=True, encoding="utf-8", errors="replace", timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return [line for line in result.stdout.splitlines() if line.startswith("test_sample.py::")]

    routine = collect("test_sample.py")
    exhaustive = collect("test_sample.py", "--exhaustive")
    assert len(exhaustive) == 13
    assert 0 < len(routine) < 12
    assert set(routine) < set(exhaustive)
    assert "test_sample.py::test_expensive" not in routine
    assert collect(routine[0]) == [routine[0]]

"""Native optional repair must leave a bounded whole-pipeline reserve."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def budget_probe(tmp_path_factory):
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("C++ compiler unavailable")
    root = Path(__file__).resolve().parents[1]
    includes = root / "aircooling/third_parties/dcCartMesh/src/mesh/dcCartMesh/dcCartCutCells"
    directory = tmp_path_factory.mktemp("point_repair_budget")
    source = directory / "probe.cpp"
    source.write_text(r'''#include "dcCartPointRepairBudget.H"
#include <cstdlib>
#include <iomanip>
#include <iostream>
int main(int argc, char** argv) {
    namespace B = Foam::dcCartPointRepairBudget;
    if (argc == 2) {
        std::cout << B::enabled(argv[1]);
    } else if (argc == 3) {
        std::cout << std::setprecision(17)
                  << B::allowance(std::strtod(argv[1], nullptr),
                                  std::strtod(argv[2], nullptr));
    } else return 2;
}
''')
    binary = directory / "probe"
    subprocess.run([compiler, "-std=c++14", "-Wall", "-Wextra", "-Werror", "-I", str(includes), str(source), "-o", str(binary)], check=True, capture_output=True, text=True)
    return binary


@pytest.mark.parametrize("flag, expected", [("1", 1), ("0", 0), ("", 0), ("true", 0), ("01", 0), ("1 ", 0)])
def test_only_explicit_one_enables_repair(budget_probe, flag, expected):
    result = subprocess.run([str(budget_probe), flag], check=True, capture_output=True, text=True)
    assert int(result.stdout) == expected


@pytest.mark.parametrize("limit, elapsed, expected", [
    (300, 0, 20), (300, 219, 20), (300, 221, 19),
    (300, 239.5, .5), (300, 240, 0), (300, 299, 0), (300, 301, 0),
    (10, 0, 0), (0, 10000, 20), (-1, 0, 0),
    ("nan", 0, 0), ("inf", 0, 0), (300, "nan", 0),
    (300, "inf", 0), (300, -1, 0),
])
def test_remaining_native_deadline_keeps_mandatory_reserve(budget_probe, limit, elapsed, expected):
    result = subprocess.run([str(budget_probe), str(limit), str(elapsed)], check=True, capture_output=True, text=True)
    assert float(result.stdout) == expected

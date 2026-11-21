"""The MPI step relay passes on whole lines only. readlines() relayed a line
the solver was still writing as its own line ("Time =" / "733"), the
convergence parser lost that iteration, and DAML's return-air verdict was
INCONCLUSIVE."""
from loguru import logger

from backend.aircooling.runner.stlgen_runner import STLGenRunner


def test_a_line_written_in_two_parts_is_relayed_whole(tmp_path, monkeypatch):
    r = STLGenRunner.__new__(STLGenRunner)
    monkeypatch.setattr(r, "_foam_env_prefix", lambda: "", raising=False)
    seen = []
    sink = logger.add(lambda m: seen.append(m.record["message"]), level="INFO")
    try:
        # "mpirun" in the command selects the log-file tail path
        r._run_cmd("printf 'Time ='; sleep 0.6; printf ' 733\\nTime = 734\\n'; true mpirun", tmp_path)
    finally:
        logger.remove(sink)
    assert "Time = 733" in seen and "Time = 734" in seen
    assert "Time =" not in seen and "733" not in seen

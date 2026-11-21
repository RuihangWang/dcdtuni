"""Frozen production raw connects the startup operator diagnosis to actual faces."""
from pathlib import Path
import sys

TOOLS=Path(__file__).resolve().parents[3]/"tools"
sys.path.insert(0,str(TOOLS))
import audit_pimple_startup_euler_support as support


def test_frozen_511_seed_predicts_the_actual_27_mismatched_faces():
    repo=TOOLS.parent
    raw=repo/"docs/benchmarks/shinaide-product-2026-10-05/evidence/pimple-startup-coupled-phi/actual-main-raw"
    report=support.audit(raw,"0.01",.01)
    assert len(report["pairs"])==1
    pair=report["pairs"][0]
    assert pair["faces"]==64
    assert len(pair["predictedNonzeroSupport"])==27
    assert pair["predictedNonzeroSupport"]==pair["writtenPhiMismatchSupport"]
    assert pair["remoteCachePairDdtMaxAbsM3s2"]==0

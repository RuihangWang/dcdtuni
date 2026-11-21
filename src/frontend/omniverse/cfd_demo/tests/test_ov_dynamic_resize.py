"""Actual supervised-child argv, native executable/GPU carriers controlled only."""
import pytest
from test_ov_stream_port import demo_command

@pytest.mark.parametrize("signal,stream,expected", [("8301",None,"47998"),("8302","48000","48000")])
def test_real_normal_launch_enables_primary_dynamic_resize_and_retains_isolation(tmp_path,signal,stream,expected):
    args=demo_command(tmp_path,stream=stream,signal=signal)
    setting="--/exts/omni.kit.livestream.app/primaryStream/allowDynamicResize=true"
    assert args.count(setting)==1
    assert f"--/exts/omni.kit.livestream.app/primaryStream/signalPort={signal}" in args
    assert f"--/exts/omni.kit.livestream.app/primaryStream/streamPort={expected}" in args
    for held in ["--/renderer/activeGpu=0","--/renderer/multiGpu/enabled=false","--/app/renderer/resolution/width=1600","--/app/renderer/resolution/height=900","--/app/window/hideUi=false","--enable","dc.cfd_bridge","--/exts/omni.cae.viz/defaultDeviceForAuto=cuda:0"]:
        assert held in args
    assert not any("spectator" in a or "allowDynamicResize=false" in a for a in args)

"""The Kit-side result transfer contract runs without Kit or a GPU."""
from __future__ import annotations

import copy
import contextlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


BRIDGE = (Path(__file__).resolve().parents[1] / 'kit/extensions/dc.cfd_bridge/dc/cfd_bridge')


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, BRIDGE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


render_scene = load('render_scene')
result_layers = load('result_layers_usd')


def result_packet():
    source = {'projectUid': 'project-a', 'projectContentSha256': 'a' * 64,
              'cfdrunSha256': 'b' * 64, 'meshReceiptSha256': 'c' * 64,
              'meshQuality': 'accepted',
              'run': {'uid': 'run-a', 'receiptSha256': 'd' * 64}}
    points = [0, 0, 0, 1, 0, 0, 0, 1, 0]
    field = {'name': 'T', 'unit': 'K'}
    lut = {'palette': 'coolwarm', 'range': [290, 310], 'opacity': 1}
    return {'schema': 'dcdtuni.render-result-layers/v1', 'revision': 4,
            'source': source, 'timeSeconds': 20,
            'coordinateSystem': {'upAxis': 'Y', 'metersPerUnit': 1, 'handedness': 'right'},
            'layers': [
                {'kind': 'meshEdges', 'layerId': 'edges', 'visible': True,
                 'meshPatchName': 'wall', 'sourceFaceUids': ['source-wall'],
                 'positions': points, 'indices': [0, 1, 1, 2, 2, 0], 'opacity': .5},
                {'kind': 'surfaceField', 'layerId': 'T', 'visible': True,
                 'meshPatchName': 'wall', 'sourceFaceUids': ['source-wall'],
                 'positions': points, 'indices': [0, 1, 2], 'values': [290, 295, 300],
                 'field': field, 'lut': lut},
                {'kind': 'slice', 'layerId': 'cut', 'visible': True, 'sourceSliceUid': 'cut-1',
                 'originMeters': [0, .5, 0], 'normal': [0, 1, 0],
                 'positions': [0, .5, 0, 1, .5, 0, 0, .5, 1], 'indices': [0, 1, 2],
                 'values': [291, 295, 299], 'field': field, 'lut': lut},
                {'kind': 'streamline', 'layerId': 'flow', 'visible': True,
                 'sourceSeedUid': 'seed-1', 'lines': [
                     {'positions': [0, 0, 0, 1, 0, 0], 'values': [0, 1]}],
                 'field': {'name': 'U', 'unit': 'm/s'}, 'tubeRadiusMeters': .01,
                 'lut': {'palette': 'viridis', 'range': [0, 1], 'opacity': 1}},
            ]}


class ResultTransferContract(unittest.TestCase):
    def test_ordered_chunks_validate_all_four_result_kinds(self):
        packet = result_packet()
        envelope = {'packet': packet, 'expectedSource': copy.deepcopy(packet['source']),
                    'expectedTimeSeconds': 20}
        text = json.dumps(envelope)
        chunks = [text[i:i + 120] for i in range(0, len(text), 120)]
        transfer = render_scene.SceneTransfer(validator=result_layers.validate_result_transfer)
        transfer.begin('request-1', len(chunks))
        for index, chunk in enumerate(chunks):
            transfer.chunk('request-1', index, chunk)
        self.assertEqual(transfer.progress('request-1'), len(chunks))
        self.assertEqual(transfer.finish('request-1'), envelope)

    def test_stale_identity_and_diagnostic_scalar_are_rejected(self):
        packet = result_packet()
        envelope = {'packet': packet, 'expectedSource': copy.deepcopy(packet['source']),
                    'expectedTimeSeconds': 20}
        envelope['expectedSource']['meshReceiptSha256'] = 'e' * 64
        with self.assertRaisesRegex(ValueError, 'source'):
            result_layers.validate_result_transfer(envelope)
        envelope['expectedSource'] = copy.deepcopy(packet['source'])
        packet['source']['meshQuality'] = 'diagnostic'
        with self.assertRaisesRegex(ValueError, 'accepted mesh quality'):
            result_layers.validate_result_transfer(envelope)

    def test_direct_forged_webrtc_claim_cannot_unlock_production_kit(self):
        packet = result_packet()
        # Even a well-formed packet with matching *self-reported* source and
        # accepted quality is not a trusted backend certificate.
        transfer = {'packet': packet, 'expectedSource': copy.deepcopy(packet['source']),
                    'expectedTimeSeconds': packet['timeSeconds']}
        result_layers.validate_result_transfer(transfer)
        with self.assertRaisesRegex(ValueError, 'Trusted renderer provenance is unavailable'):
            result_layers.require_trusted_result_context(transfer, None)
        with self.assertRaisesRegex(ValueError, 'differs from trusted'):
            result_layers.require_trusted_result_context(transfer, {
                'source': {**packet['source'], 'meshReceiptSha256': 'e' * 64},
                'timeSeconds': 20,
            })

    def test_kit_command_rejects_forged_packet_before_usd_authoring(self):
        packet = result_packet()
        transfer = {'packet': packet, 'expectedSource': copy.deepcopy(packet['source']),
                    'expectedTimeSeconds': 20}
        omni = types.ModuleType('omni'); omni.__path__ = []
        ext = types.ModuleType('omni.ext'); ext.IExt = object
        usd = types.ModuleType('omni.usd')
        scene = types.SimpleNamespace(GetCustomDataByKey=lambda key: 'project-a')
        stage = types.SimpleNamespace(GetPrimAtPath=lambda path: scene)
        usd.get_context = lambda: types.SimpleNamespace(get_stage=lambda: stage)
        kit = types.ModuleType('omni.kit'); kit.__path__ = []
        app = types.ModuleType('omni.kit.app')
        kit.app = app; omni.ext = ext; omni.usd = usd; omni.kit = kit
        carb = types.ModuleType('carb'); carb.__path__ = []
        events = types.ModuleType('carb.events')
        dispatcher = types.ModuleType('carb.eventdispatcher')
        carb.events = events; carb.eventdispatcher = dispatcher
        stubs = {'omni': omni, 'omni.ext': ext, 'omni.usd': usd,
                 'omni.kit': kit, 'omni.kit.app': app,
                 'carb': carb, 'carb.events': events,
                 'carb.eventdispatcher': dispatcher}
        path = str(BRIDGE.parent)
        with mock.patch.dict(sys.modules, stubs):
            sys.path.insert(0, path)
            try:
                bridge = importlib.import_module('cfd_bridge.extension')
                instance = bridge.CfdBridgeExtension()
                instance._result_transfer = types.SimpleNamespace(finish=lambda request_id: transfer)
                instance._trusted_result_context = None
                instance._render_project_hash = packet['source']['projectContentSha256']
                replies = []
                instance._render_ack = lambda request_id, **data: replies.append((request_id, data))
                with contextlib.redirect_stdout(io.StringIO()):
                    instance._route_render_command('beginRenderResultLayers',
                        json.dumps({'requestId': 'forged-begin', 'chunks': 1}))
                    instance._route_render_command('loadRenderResultLayers', json.dumps({'requestId': 'forged'}))
                instance._trusted_result_context = {
                    'source': copy.deepcopy(packet['source']), 'timeSeconds': 20}
                instance._render_project_hash = 'e' * 64
                with contextlib.redirect_stdout(io.StringIO()):
                    instance._route_render_command('loadRenderResultLayers', json.dumps({'requestId': 'stale'}))
            finally:
                sys.path.remove(path)
        # Since ca1cee61f (2026-10-06) the bridge loads only the server's own
        # canonical artifact, so a browser-sent result packet is refused at the
        # command itself -- with or without a trusted context, for the ACKed
        # scene or a stale one -- before any provenance or USD work.
        self.assertEqual([r[0] for r in replies], ['forged-begin', 'forged', 'stale'])
        for _request, reply in replies:
            self.assertFalse(reply['ok'])
            self.assertIn('Result layers require a verified server canonical grant', reply['error'])


if __name__ == '__main__':
    unittest.main()

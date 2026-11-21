"""Owned public inner-Frame lifecycle; no encoded layout or pixel authority.

Default off. Only lifecycle preparation can install a delegate; sampling never
changes SDK objects. The reviewed SDK exposes no complete current stream layout
producer, so capture_current deliberately cannot return a positive transform.
"""
from dataclasses import dataclass
import hashlib
import importlib
import itertools
import json
from pathlib import Path

from .render_output_observations import (
    _frame, _matrix, _owner, _positive_pair, _same_public_api,
    reviewed_runtime_getters,
)

_serial = itertools.count(1)


@dataclass(frozen=True)
class LayoutLease:
    """Local owner epoch, never an encoder/frame/permission identifier."""
    owner: object
    generation: int
    key: str

    def same(self, other):
        return (type(other) is LayoutLease and self.owner is other.owner
                and self.generation == other.generation and self.key == other.key)


def same_layout_lease(a, b):
    return type(a) is LayoutLease and a.same(b)


def _value(value):
    # Local immutable comparison image, not outgoing metadata or wire authority.
    raw = json.dumps(value, sort_keys=True, allow_nan=False,
                     ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > 16384:
        raise ValueError('owned view/context limit')
    return raw


def reviewed_layout_runtime():
    """Gate public installed sources/classes before making an owned delegate.

    This binds disk cohort and loaded classes, not extension memory to disk or
    an encoder source. Unknown cohorts cannot install anything.
    """
    getters = reviewed_runtime_getters()
    if getters is None:
        return None
    module = importlib.import_module('omni.kit.widget.viewport.display_delegate')
    p = Path(module.__file__).resolve()
    if (p.parent.parent.parent.parent.parent.name != 'omni.kit.widget.viewport-e33603573147cc56'
            or hashlib.sha256(p.read_bytes()).hexdigest() !=
            '47ebdf1077aa854f366e9c2d7208e5ee1b96dd6cb75384aeae7aa4a6f18449ba'):
        return None
    ui = importlib.import_module('omni.ui')
    base = module.ViewportDisplayDelegate
    if not isinstance(base, type):
        return None
    return dict(getters=getters, make_delegate=lambda: _delegate(ui, base),
                supports_provider=lambda p: isinstance(p, ui.ImageProvider),
                expected_policy=ui.IwpFillPolicy.IWP_PRESERVE_ASPECT_FIT,
                expected_alignment=ui.Alignment.CENTER)


def _delegate(ui, base):
    class OwnedDelegate(base):
        def __init__(self):
            # Base private storage is not touched. Only these owned UI objects
            # are created/destroyed; the ImageProvider is always borrowed.
            self.frame = None
            self.image = None
            self.stack = None
            self.provider = None
            self.updates = 0

        @property
        def image_provider(self):
            return self.provider

        def create(self, ui_frame, previous, *args, **kwargs):
            provider = previous.image_provider
            self.destroy()
            self.frame, self.provider = ui_frame, provider
            with ui_frame:
                self.stack = ui.ZStack()
                with self.stack:
                    self.image = ui.ImageWithProvider(provider,
                        style_type_name_override='ViewportImage', name='Color',
                        fill_policy=ui.IwpFillPolicy.IWP_PRESERVE_ASPECT_FIT,
                        alignment=ui.Alignment.CENTER)
            return self.stack

        def update(self, viewport_api, texture, view, projection,
                   presentation_key=0, metadata=None):
            # Opaque resource forwards directly; no holding/copy/extraction.
            self.provider.set_image_data(texture, presentation_key=presentation_key,
                                         metadata=metadata)
            self.updates += 1
            return self.size

        @property
        def size(self):
            if self.image is None:
                return 0, 0
            return int(self.image.computed_width), int(self.image.computed_height)

        def destroy(self):
            stack = self.stack
            self.stack = self.image = self.frame = self.provider = None
            self.updates = 0
            if stack is not None:
                stack.destroy()  # its own child tree; never provider.destroy()

    return OwnedDelegate()


class OwnedOutputLayout:
    def __init__(self, enabled=False, runtime_factory=reviewed_layout_runtime):
        self.enabled = enabled is True
        self._runtime_factory = runtime_factory
        self._binding = None
        self._generation = 0
        self._nonce = object()
        self._id = next(_serial)
        self._closed = False
        self.lifecycle_error = False

    def _lease(self):
        return LayoutLease(self._nonce, self._generation,
                           'native-layout:%d:%d' % (self._id, self._generation))

    def _retire_sample(self):
        self._generation += 1
        return None

    def prepare(self, api, window, stage, camera, read_context, read_view, paired):
        """Explicit lifecycle action, never called from capture_current."""
        if self._closed or not self.enabled:
            return False
        try:
            runtime = self._runtime_factory()
            if runtime is None:
                self.invalidate()
                return False
            getters = runtime['getters']
            if (getters['supports_viewport'](api) is not True
                    or getters['supports_window'](window) is not True):
                self.invalidate()
                return False
            owner, widget = _owner(api, window, stage, camera)
            app = window.app_window
            if app is None or getters['supports_appwindow'](app) is not True:
                self.invalidate()
                return False
            active_api, active_window = paired()
            if active_window is not window or not _same_public_api(active_api, api):
                self.invalidate()
                return False
            context, view = _value(read_context()), _value(read_view())
            old = self._binding
            if (old and old['window'] is window and old['widget'] is widget
                    and old['app'] is app and _same_public_api(old['api'], api)
                    and owner == old['owner']
                    and widget.display_delegate is old['delegate']):
                if context != old['context'] or view != old['view']:
                    self._generation += 1
                old.update(context=context, view=view, read_context=read_context,
                           read_view=read_view, paired=paired)
                return True
            self.invalidate()
            previous = widget.display_delegate
            provider = previous.image_provider
            if runtime['supports_provider'](provider) is not True:
                return False
            delegate = runtime['make_delegate']()
            # Store before setter/create so an incomplete create can be restored.
            self._binding = dict(api=api, window=window, widget=widget, app=app,
                stage=stage, camera=camera, owner=owner, context=context, view=view,
                read_context=read_context, read_view=read_view, paired=paired,
                runtime=runtime, previous=previous, delegate=delegate, provider=provider)
            try:
                widget.display_delegate = delegate
            except Exception:
                # The reviewed setter clears Frame before calling create. If
                # create fails, its delegate pointer can still be previous.
                if widget.display_delegate is previous and delegate.frame is not None:
                    previous.create(delegate.frame, delegate)
                raise
            # Installation itself may trigger callbacks. Pin public identities
            # before the last immutable context/view fence; never keep a UI
            # delegate installed for a replaced active viewport/window.
            final_owner, final_widget = _owner(api, window, stage, camera)
            final_delegate = final_widget.display_delegate
            final_app = window.app_window
            final_api, final_window = paired()
            same_api = _same_public_api(final_api, api)
            final_view = _value(read_view())
            final_context = _value(read_context())
            if (self._binding is None or self._binding['delegate'] is not delegate
                    or final_owner != owner or final_widget is not widget
                    or final_delegate is not delegate or final_app is not app
                    or final_window is not window or not same_api
                    or final_view != view or final_context != context):
                raise ValueError('owned delegate install context changed')
            self._generation += 1
            return True
        except Exception:
            self.invalidate()
            return False

    def invalidate(self):
        self._generation += 1
        binding, self._binding = self._binding, None
        if binding is None:
            return
        widget, delegate = binding['widget'], binding['delegate']
        try:
            if widget.display_delegate is delegate:
                widget.display_delegate = binding['previous']
        except Exception:
            self.lifecycle_error = True
        finally:
            try:
                delegate.destroy()
            except Exception:
                self.lifecycle_error = True

    def close(self):
        if not self._closed:
            self._closed = True
            self.invalidate()

    def observe_native_current(self, api, window, app):
        """Pinned native raw measurements only; no physical/stream transform."""
        b = self._binding
        if self._closed or not self.enabled or b is None:
            return None
        try:
            generation = self._generation
            if (window is not b['window'] or app is not b['app']
                    or not _same_public_api(api, b['api'])):
                return self._retire_sample()
            getters = b['runtime']['getters']
            owner, widget = _owner(api, window, b['stage'], b['camera'])
            if widget is not b['widget'] or owner != b['owner']:
                return self._retire_sample()
            paired_api, paired_window = b['paired']()
            if paired_window is not window or not _same_public_api(paired_api, api):
                return self._retire_sample()
            widget_api = widget.viewport_api
            delegate = b['delegate']
            frame, image, provider = delegate.frame, delegate.image, delegate.provider
            if (widget.display_delegate is not delegate or window.app_window is not app
                    or frame is None or image is None or provider is not b['provider']
                    or delegate.updates <= 0 or provider.is_reference_valid is not True):
                return self._retire_sample()
            native_time = api.time
            initial_frame = _frame(api)
            from .render_output_observations import _dimensions
            canvas = (_matrix(api.view), _matrix(api.projection),
                      _matrix(api.transform), _dimensions(api.resolution), _dimensions(api.full_resolution))
            def values():
                from .render_output_observations import _number, _dimensions
                if (getters['is_app_window_attached'](app) is not True
                        or image.fill_policy != b['runtime']['expected_policy']
                        or image.alignment != b['runtime']['expected_alignment']):
                    raise ValueError('unsupported native image policy/window')
                return dict(frameRaw=[_number(frame.screen_position_x), _number(frame.screen_position_y),
                        *_positive_pair(frame.computed_content_width, frame.computed_content_height)],
                    imageRaw=[_number(image.screen_position_x), _number(image.screen_position_y),
                        *_positive_pair(image.computed_content_width, image.computed_content_height)],
                    imagePolicy=str(image.fill_policy), imageAlignment=str(image.alignment),
                    textureSize=_dimensions((provider.width, provider.height)),
                    framebufferSize=_dimensions((getters['get_framebuffer_width'](app), getters['get_framebuffer_height'](app))),
                    scales=_positive_pair(app.get_dpi_scale(), app.get_ui_scale()))
            measured, final_measured = values(), values()
            # All final identity getters precede the final frame/time/view fence.
            final_owner, final_widget = _owner(api, window, b['stage'], b['camera'])
            final_widget_api = final_widget.viewport_api
            final_delegate = final_widget.display_delegate
            final_app = window.app_window
            final_api, final_window = b['paired']()
            same_api = _same_public_api(final_api, api)
            final_frame, final_image, final_provider = delegate.frame, delegate.image, delegate.provider
            final_valid = provider.is_reference_valid
            final_canvas = (_matrix(api.view), _matrix(api.projection),
                            _matrix(api.transform), _dimensions(api.resolution), _dimensions(api.full_resolution))
            final_delivered = _frame(api)
            final_time = api.time
            final_view = _value(b['read_view']())
            final_context = _value(b['read_context']())
            if (self._binding is not b or self._generation != generation
                    or final_owner != owner or final_widget is not widget
                    or final_widget_api is not widget_api or final_delegate is not delegate
                    or final_app is not app or final_window is not window or not same_api
                    or final_frame is not frame or final_image is not image or final_provider is not provider
                    or final_valid is not True or measured != final_measured
                    or final_canvas != canvas or final_delivered != initial_frame or final_time != native_time
                    or final_context != b['context'] or final_view != b['view']):
                return self._retire_sample()
            return dict(lease=self._lease(), measurements=measured,
                        deliveredFrame=initial_frame, canvas=canvas,
                        layoutProof='unresolved', physicalRectProof='unresolved',
                        frameSourceBindingProof='unresolved', pixelVerified=False)
        except Exception:
            return self._retire_sample()

    def current_lease(self):
        b = self._binding
        if b is None:
            return None
        sample = self.observe_native_current(b['api'], b['window'], b['app'])
        return sample['lease'] if sample is not None else None

    def capture_current(self, api, window, app):
        # Native Frame observations do not establish UI-unit→framebuffer and
        # framebuffer→current decoded-stream ownership/crop/scale. No producer
        # for these segments is reviewed/installed in this implementation.
        self.observe_native_current(api, window, app)
        return None

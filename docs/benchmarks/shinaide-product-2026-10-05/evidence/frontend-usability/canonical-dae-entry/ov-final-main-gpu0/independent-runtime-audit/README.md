# Independent normal OV restart and owned cache verification

Normal main launch source 7a74dc80 is bound to six exact deployed source/test
bytes. Kit PID 3598531 (startticks 124356249), its supervisor, launcher and
backend are alive; the owned kit.ready marker names signaling port 8301 and
UID 1002. Actual listener ownership was read from /proc, without connecting
or altering a service. Kit and its launch chain use CPU 0–7.

The actual Kit environment sets render-textures and TMPDIR under its runtime,
and XDG/CUDA caches under main/.codex-tmp/ov-cache. All six resolved directories
stay inside main. The captured real scene authored 2154 primitives and wrote
36 texture files (full paths/bytes/SHA in owned-textures-final.json). At that
cache snapshot, all 203 pre-existing /tmp texture files retained their exact
startup-request SHA; no files were added, removed or changed there. The three
parent-owned previous new files had been preserved before removal; their
preserved copies were independently hashed in the first cache report.

Four existing parent-owned nvidia-smi snapshot files are copied byte-exact.
Their sole process row is this Kit on GPU0 (CUDA UUID dd97..., PCI16), C+G,
4346 MiB. GPU0 total is 4388 MiB. The other GPUs retain only the displayed
background 1 MiB (GPU6 4 MiB). Kit's Vulkan inventory lists eight devices, but
only GPU0 is Active; descriptor enumeration of all devices must not be treated
as occupancy. No new GPU query or workload was issued by this audit.

The bounded log excerpts bind captured byte prefixes, omit scene chunks/base64
and do not copy grants. App ready occurred at 47.491 seconds; the primary
stream listener started normally. The new request 5cccd441-506d-48c4-b3b0-771c54ab4c4a
uses engineering hash 043c4ba02c7a73ce89898fa8e4e7a1483443a5060851e477c90b14e5906fc210.
The scene authored at 10:51:30Z. SciPy/NumPy warnings and unselected CUDA-device
enumeration warnings are retained rather than hidden; no ordinary exception
traceback was found in the captured prefixes.

This audit establishes normal process/readiness/cache metadata and real
repository-local texture writes. Browser ACK, decoded video, screenshot,
camera and renderer-switch behavior remain the parent's independent live UI
evidence. It does not prove every filesystem write globally stays in main.
No source, index, service, browser, Kit, GPU or API operation was performed.

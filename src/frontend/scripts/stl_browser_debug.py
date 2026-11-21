#!/usr/bin/env python3
"""
Headless browser automation for STL_generate.html debugging.

Launches STL_generate.html in headless Chromium via Playwright, captures
console logs, executes JS commands, takes screenshots, and simulates
user interactions. This lets Claude debug the frontend automatically.

Usage:
    python scripts/stl_browser_debug.py                    # Open + screenshot
    python scripts/stl_browser_debug.py --load-proj FILE   # Load a .cfdproj
    python scripts/stl_browser_debug.py --run-js "code"    # Execute JS
    python scripts/stl_browser_debug.py --click "#btn-id"  # Click element
    python scripts/stl_browser_debug.py --export-cfd       # Trigger CFD export
    python scripts/stl_browser_debug.py --interactive       # REPL mode
"""
import argparse
import json
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # src/frontend/
HTML_PATH = PROJECT_ROOT / "web" / "STL_generate.html"
SCREENSHOT_DIR = PROJECT_ROOT / "scripts" / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)


class STLBrowserDebugger:
    """Headless browser controller for STL_generate.html."""

    def __init__(self, headless=True, slow_mo=0):
        self.headless = headless
        self.slow_mo = slow_mo
        self.console_logs = []
        self.errors = []
        self.pw = None
        self.browser = None
        self.page = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()

    def start(self):
        """Launch browser and open STL_generate.html."""
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(
            headless=self.headless,
            args=['--no-sandbox', '--disable-gpu', '--disable-software-rasterizer']
        )
        self.page = self.browser.new_page(viewport={"width": 1920, "height": 1080})

        # Capture console output
        self.page.on("console", self._on_console)
        self.page.on("pageerror", self._on_error)

        # Navigate to the HTML file
        url = f"file://{HTML_PATH}"
        print(f"[DEBUG] Opening: {url}")
        self.page.goto(url, wait_until="networkidle", timeout=30000)
        print(f"[DEBUG] Page loaded. Title: {self.page.title()}")

        # Wait for Three.js to initialize
        self.page.wait_for_function("typeof THREE !== 'undefined'", timeout=10000)
        print(f"[DEBUG] Three.js loaded")

        # Small delay for full initialization
        self.page.wait_for_timeout(1000)
        return self

    def stop(self):
        """Close browser."""
        if self.browser:
            self.browser.close()
        if self.pw:
            self.pw.stop()

    def _on_console(self, msg):
        """Capture console.log/warn/error."""
        entry = f"[{msg.type}] {msg.text}"
        self.console_logs.append(entry)
        if msg.type in ("error", "warning"):
            print(f"  CONSOLE {entry}")

    def _on_error(self, error):
        """Capture page errors."""
        entry = f"[PAGE_ERROR] {error.message}"
        self.errors.append(entry)
        print(f"  ERROR: {entry}")

    def screenshot(self, name="debug"):
        """Take a screenshot."""
        path = SCREENSHOT_DIR / f"{name}.png"
        self.page.screenshot(path=str(path), full_page=False)
        print(f"[SCREENSHOT] {path}")
        return str(path)

    def run_js(self, code):
        """Execute JavaScript and return the result."""
        try:
            result = self.page.evaluate(code)
            return result
        except Exception as e:
            print(f"[JS_ERROR] {e}")
            return None

    def get_state_summary(self):
        """Get a summary of the current state."""
        return self.run_js("""(() => {
            const s = state;
            const models = s.models.map(m => ({
                id: m.id, kind: m.kind, name: m.name, enabled: m.enabled,
                anchor: m.anchor, base: m.base,
                patchCount: Object.values(m.faces).reduce((n, f) => n + f.patches.length, 0),
                cfd: m.cfd || null,
                equipmentCount: m.rack ? (m.rack.equipments || []).length : 0
            }));
            return {
                modelCount: s.models.length,
                models: models,
                inputUnit: s.inputUnit,
                selection: s.selection,
                cfdRoom: s.cfdRoom || null
            };
        })()""")

    def get_console_logs(self, clear=False):
        """Get captured console logs."""
        logs = list(self.console_logs)
        if clear:
            self.console_logs.clear()
        return logs

    def get_errors(self):
        """Get captured errors."""
        return list(self.errors)

    def click_button(self, selector):
        """Click a button by CSS selector or text."""
        try:
            self.page.click(selector, timeout=5000)
            self.page.wait_for_timeout(500)
            print(f"[CLICK] {selector}")
            return True
        except Exception as e:
            print(f"[CLICK_FAILED] {selector}: {e}")
            return False

    def click_button_by_text(self, text):
        """Click a button by its visible text."""
        try:
            self.page.click(f"button:has-text('{text}')", timeout=5000)
            self.page.wait_for_timeout(500)
            print(f"[CLICK_TEXT] '{text}'")
            return True
        except Exception as e:
            print(f"[CLICK_TEXT_FAILED] '{text}': {e}")
            return False

    def load_project(self, cfdproj_path):
        """Load a .cfdproj file programmatically."""
        cfdproj_path = Path(cfdproj_path).resolve()
        print(f"[LOAD] Loading project: {cfdproj_path}")

        with open(cfdproj_path) as f:
            proj_data = f.read()

        # Inject the project data directly via JS (bypass file dialog)
        # Skip rendering functions that need WebGL in headless mode
        result = self.run_js(f"""(() => {{
            try {{
                const raw = JSON.parse({json.dumps(proj_data)});
                state = normalizeState(raw);
                syncCounters();
                try {{ restoreGeometryCache(); }} catch(e) {{}}
                try {{ rebuildGeometry(); }} catch(e) {{}}
                return {{ success: true, modelCount: state.models.length }};
            }} catch(e) {{
                return {{ success: false, error: e.message }};
            }}
        }})()""")
        print(f"[LOAD] Result: {result}")
        return result

    def create_model(self, kind):
        """Create a new model of given kind.
        Uses inner factory functions directly (window.* may not be available in headless mode
        due to WebGL errors halting script execution)."""
        factory_map = {
            'box': 'createBoxModel',
            'rack': 'createRackModel',
            'acu': 'createAcuModel',
            'wall': 'createWallModel',
            'baffle': 'createBaffleModel',
            'floor': 'createFloorModel',
        }
        factory = factory_map.get(kind)
        if not factory:
            print(f"[ERROR] Unknown kind: {kind}")
            return None
        count = self.run_js(f"state.models.filter(m => m.kind === '{kind}').length") or 0
        name = f"{'ACU' if kind == 'acu' else kind.capitalize()}_{count + 1}"
        result = self.run_js(f"""(() => {{
            const model = {factory}('{name}');
            state.models.push(model);
            return {{ id: model.id, kind: model.kind, name: model.name, cfd: model.cfd,
                      anchor: model.anchor, base: model.base, patchCount: 0, equipmentCount: 0 }};
        }})()""")
        if result:
            print(f"[CREATE] {kind} -> {result['name']} ({result['id']})")
        else:
            print(f"[CREATE] {kind} -> FAILED")
        return result

    def select_model(self, model_id):
        """Select a model by ID."""
        self.run_js(f"setSelection('model', '{model_id}')")
        self.page.wait_for_timeout(300)

    def add_patch_to_face(self, model_id, face_id, patch_type='rect'):
        """Add a patch to a model face using inner functions directly."""
        result = self.run_js(f"""(() => {{
            const model = getModelById('{model_id}');
            if (!model) return null;
            const face = model.faces['{face_id}'];
            if (!face) return null;
            const id = nextPatchId();
            const patch = {{
                id: id,
                type: '{patch_type}',
                name: '{patch_type.capitalize()}_Patch_' + id.replace('p_',''),
                color: '#e06c75',
                showVector: false, vx: 0, vy: 0, vz: 1
            }};
            if ('{patch_type}' === 'rect') {{
                patch.x = 0; patch.y = 0;
                patch.w = face.wType === 'W' ? model.base.W * 0.8 : model.base.D * 0.8;
                patch.h = face.hType === 'H' ? model.base.H * 0.5 : (face.hType === 'D' ? model.base.D * 0.8 : model.base.W * 0.8);
            }} else {{
                const fw = face.wType === 'W' ? model.base.W : model.base.D;
                const fh = face.hType === 'H' ? model.base.H : (face.hType === 'D' ? model.base.D : model.base.W);
                patch.cx = fw / 2; patch.cy = fh / 2; patch.r = Math.min(fw, fh) * 0.25;
            }}
            face.patches.push(patch);
            return patch;
        }})()""")
        print(f"[ADD_PATCH] {model_id}/{face_id}/{patch_type} -> {result and result.get('id')}")
        return result

    def export_cfd_json(self):
        """Trigger CFD export and capture the JSON (without downloading ZIP)."""
        result = self.run_js("""(() => {
            try {
                const json = buildCfdExportJson();
                return { success: true, data: json };
            } catch(e) {
                return { success: false, error: e.message, stack: e.stack };
            }
        })()""")
        if result and result.get('success'):
            print(f"[EXPORT] CFD JSON generated successfully")
            data = result['data']
            print(f"  Racks: {len(data.get('racks', {}))}")
            print(f"  ACUs: {len(data.get('acus', {}))}")
            print(f"  Baffles: {len(data.get('baffles', {}))}")
            print(f"  Floors: {len(data.get('floors', {}))}")
            print(f"  Walls: {len(data.get('walls', {}))}")
        else:
            print(f"[EXPORT_FAILED] {result}")
        return result

    def get_inspector_html(self):
        """Get the current inspector panel HTML."""
        return self.run_js("document.getElementById('prop-inspector')?.innerHTML || ''")

    def interactive_repl(self):
        """Interactive REPL for debugging."""
        print("\n=== STL Browser Debug REPL ===")
        print("Commands:")
        print("  js <code>          - Execute JavaScript")
        print("  state              - Show state summary")
        print("  screenshot [name]  - Take screenshot")
        print("  logs               - Show console logs")
        print("  errors             - Show errors")
        print("  create <kind>      - Create model (box/rack/acu/wall/baffle/floor)")
        print("  load <path>        - Load .cfdproj file")
        print("  export             - Generate CFD export JSON")
        print("  select <model_id>  - Select a model")
        print("  patch <model_id> <face_id> [type] - Add patch")
        print("  inspector          - Show inspector HTML (first 500 chars)")
        print("  quit               - Exit")
        print()

        while True:
            try:
                cmd = input("stl> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not cmd:
                continue
            parts = cmd.split(maxsplit=1)
            verb = parts[0].lower()

            if verb == 'quit' or verb == 'exit':
                break
            elif verb == 'js' and len(parts) > 1:
                result = self.run_js(parts[1])
                print(json.dumps(result, indent=2, default=str) if result else "null")
            elif verb == 'state':
                s = self.get_state_summary()
                print(json.dumps(s, indent=2, default=str))
            elif verb == 'screenshot':
                name = parts[1] if len(parts) > 1 else f"debug_{int(time.time())}"
                self.screenshot(name)
            elif verb == 'logs':
                for log in self.get_console_logs():
                    print(f"  {log}")
            elif verb == 'errors':
                for err in self.get_errors():
                    print(f"  {err}")
            elif verb == 'create' and len(parts) > 1:
                self.create_model(parts[1])
            elif verb == 'load' and len(parts) > 1:
                self.load_project(parts[1])
            elif verb == 'export':
                r = self.export_cfd_json()
                if r and r.get('success'):
                    # Save to file
                    out = SCREENSHOT_DIR / "cfd_export_debug.json"
                    with open(out, 'w') as f:
                        json.dump(r['data'], f, indent=2)
                    print(f"  Saved to: {out}")
            elif verb == 'select' and len(parts) > 1:
                self.select_model(parts[1])
            elif verb == 'patch':
                args = cmd.split()
                if len(args) >= 3:
                    pt = args[3] if len(args) > 3 else 'rect'
                    self.add_patch_to_face(args[1], args[2], pt)
            elif verb == 'inspector':
                html = self.get_inspector_html()
                print(html[:500] if html else "(empty)")
            else:
                print(f"Unknown command: {cmd}")


def main():
    parser = argparse.ArgumentParser(description="STL_generate.html browser debugger")
    parser.add_argument("--load-proj", type=str, help="Load a .cfdproj file")
    parser.add_argument("--run-js", type=str, help="Execute JavaScript code")
    parser.add_argument("--click", type=str, help="Click element by CSS selector")
    parser.add_argument("--click-text", type=str, help="Click button by text")
    parser.add_argument("--create", type=str, help="Create model (box/rack/acu/wall/baffle/floor)")
    parser.add_argument("--export-cfd", action="store_true", help="Export CFD JSON")
    parser.add_argument("--state", action="store_true", help="Print state summary")
    parser.add_argument("--screenshot", type=str, nargs='?', const="debug", help="Take screenshot")
    parser.add_argument("--interactive", action="store_true", help="Interactive REPL")
    parser.add_argument("--headed", action="store_true", help="Show browser window")
    args = parser.parse_args()

    with STLBrowserDebugger(headless=not args.headed) as dbg:
        if args.load_proj:
            dbg.load_project(args.load_proj)

        if args.create:
            dbg.create_model(args.create)

        if args.run_js:
            result = dbg.run_js(args.run_js)
            print(json.dumps(result, indent=2, default=str) if result else "null")

        if args.click:
            dbg.click_button(args.click)

        if args.click_text:
            dbg.click_button_by_text(args.click_text)

        if args.export_cfd:
            result = dbg.export_cfd_json()
            if result and result.get('success'):
                out = SCREENSHOT_DIR / "cfd_export_debug.json"
                with open(out, 'w') as f:
                    json.dump(result['data'], f, indent=2)
                print(f"Saved to: {out}")

        if args.state:
            s = dbg.get_state_summary()
            print(json.dumps(s, indent=2, default=str))

        if args.screenshot is not None:
            dbg.screenshot(args.screenshot)

        if args.interactive:
            dbg.interactive_repl()

        # Default: if no specific action, just screenshot and show state
        if not any([args.load_proj, args.run_js, args.click, args.click_text,
                     args.create, args.export_cfd, args.state,
                     args.screenshot is not None, args.interactive]):
            dbg.screenshot("initial")
            s = dbg.get_state_summary()
            print(json.dumps(s, indent=2, default=str))

        # Always print errors if any
        if dbg.errors:
            print(f"\n=== {len(dbg.errors)} ERRORS ===")
            for err in dbg.errors:
                print(f"  {err}")

        print(f"\nConsole logs: {len(dbg.console_logs)} entries")


if __name__ == "__main__":
    main()

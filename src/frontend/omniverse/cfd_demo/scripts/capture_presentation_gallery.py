#!/usr/bin/env python3
"""Record the five original visualisation modes from the real OV stream."""
import asyncio
import json
import os
from pathlib import Path
import time
import hotspot_record as h

ROOT = Path(__file__).resolve().parents[5]
OUT = Path(os.environ["PRESENTATION_GALLERY"])
SCENE = ROOT / "src/backend/kit-cae/data/GTC/GeoUSD/GTC2025.json"
DATA = ROOT / ("src/frontend/omniverse/cfd_demo/SCANO/GTC_A3_Numerical/logger/"
               "inference_results/prediction_case0_2026-08-04-22-11-37.vti")

async def main():
    from playwright.async_api import async_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=h.CHROME, headless=False,
            args=["--disable-gpu","--use-gl=swiftshader","--no-sandbox",
                  "--disable-dev-shm-usage","--window-size=1920,1080",
                  "--autoplay-policy=no-user-gesture-required"])
        page = await browser.new_page(viewport={"width":1920,"height":1080})
        try:
            await page.goto(h.URL, wait_until="domcontentloaded")
            await page.wait_for_function(
                "() => {const v=document.getElementById('remote-video');"
                "return v && v.videoWidth && v.readyState>=2}",timeout=90000)
            await page.wait_for_timeout(4000)
            pos=h.ksize()
            await h.send(page,"loadScene",{"path":str(SCENE)})
            assert await h.wait_kit(page,"presentationCamera","gallery camera",pos,300)
            await page.wait_for_timeout(8000)
            await h.snapshot(page,OUT/"ov_geometry.png")
            h.log("gallery geometry captured")
            # Volume Off restores the translucent Room shell. Use a separate
            # shell-free reference so the readiness check sees the field itself.
            await h.send(page,"setObjectVisible",{"name":"Room","visible":False})
            await page.wait_for_timeout(4000)
            ref=OUT/"_field_ref.png"
            await h.snapshot(page,ref)

            pos=h.ksize()
            await h.send(page,"loadDataset",{"path":str(DATA)})
            assert await h.wait_kit(page,"load_dataset OK","gallery dataset",pos,240)

            async def volume(field,name):
                await h.send(page,"setVolume",{"field":field,"visible":False})
                await h.send(page,"setObjectVisible",{"name":"Room","visible":False})
                cleared=await h.wait_until_similar(page,ref,OUT/"_probe.png",
                                                   "gallery field cleared",3.0,90)
                assert cleared is not None, "Previous field did not clear"
                pos=h.ksize()
                await h.send(page,"setVolume",{"field":field,"visible":True})
                assert await h.wait_kit(page,"volume rebuilt","gallery field",pos,240)
                if field=="T":
                    await h.send(page,"setVolumeRange",{"min":292.1,"max":309.0})
                else:
                    await h.send(page,"setVolumeRange",{"min":0.0,"max":1.5})
                visible=await h.wait_until_changed(page,ref,OUT/"_probe.png",
                                                   "gallery field visible",3.0,120)
                assert visible is not None, "Field absent"
                await page.wait_for_timeout(3000)
                await h.snapshot(page,OUT/name)
                h.log("gallery "+name+" captured")

            await volume("T","ov_temp.png")
            await volume("U","ov_velocity.png")
            await volume("T","_roi_full.png")
            await h.send(page,"setRoiBox",{"min":[0,0,0],"max":[16,5,19.2]})
            await h.send(page,"setRoiVisible",{"visible":True})
            pos=h.ksize()
            await h.send(page,"setRoiClip",{"enabled":True})
            assert await h.wait_kit(page,"volume rebuilt","gallery ROI",pos,240)
            await h.send(page,"setVolumeRange",{"min":292.1,"max":309.0})
            await page.wait_for_timeout(16000)
            await h.snapshot(page,OUT/"ov_roi.png")
            h.log("gallery ROI captured")

            await h.send(page,"setRoiClip",{"enabled":False})
            await h.send(page,"setRoiVisible",{"visible":False})
            await h.send(page,"setVolume",{"field":"T","visible":False})
            await h.send(page,"setObjectVisible",{"name":"Room","visible":False})
            await page.wait_for_timeout(12000)
            pos=h.ksize()
            await h.send(page,"setStreamlines",{
                "enabled":True,"field":"T","seedCenter":h.SEED["center"],
                "seedRadius":h.SEED["radius"],"nSeeds":h.SEED["nSeeds"],
                "maxSteps":h.SEED["maxSteps"],"direction":"forward"})
            assert await h.wait_kit(page,"streamlines generated","gallery streamlines",pos,240)
            await page.wait_for_timeout(7000)
            await h.snapshot(page,OUT/"ov_streamlines.png")
            (OUT/"sources.json").write_text(json.dumps({
                "geometry":str(SCENE),"dataset":str(DATA),
                "camera":json.loads(Path(os.environ["DC_CFD_PRESENTATION_CAMERA"]).read_text()),
                "temperatureDomainK":[292.1,309.0],"velocityDomainMps":[0,1.5],
                "roiMin":[0,0,0],"roiMax":[16,5,19.2],
                "capture":"Decoded Omniverse WebRTC stream","unixTime":time.time()
            },indent=2))
            h.log("gallery complete")
        finally:
            await browser.close()

if __name__=="__main__":
    asyncio.run(main())

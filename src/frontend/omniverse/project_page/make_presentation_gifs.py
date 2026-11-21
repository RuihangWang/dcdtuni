#!/usr/bin/env python3
"""Export presentation GIFs and MP4s from unlabelled OV renders.
The camera belongs to the source renders. Cross-fades are presentation transitions.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
FPS = 20

def caption(im, text, colour):
    im = im.copy().convert("RGB")
    d = ImageDraw.Draw(im)
    size = round(im.width * 30 / 900)
    f = ImageFont.truetype(FONT, size)
    x, margin = round(im.width * 26 / 900), round(im.width * 24 / 900)
    bb = d.textbbox((0, 0), text, font=f)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    y = im.height - th - margin
    pad = round(im.width * 12 / 900)
    d.rounded_rectangle((x-pad, y-pad, x+tw+pad, y+th+pad),
                       radius=round(im.width*9/900), fill=(10,12,16),
                       outline=colour, width=max(2,round(im.width*2/900)))
    d.text((x,y-bb[1]),text,font=f,fill=colour)
    return im

def sequence(paths, labels, colour, crop_ratio=None):
    imgs=[Image.open(p).convert("RGB") for p in paths]
    if crop_ratio:
        width=int(imgs[0].height*crop_ratio)
        width-=width%2
        left=(imgs[0].width-width)//2
        assert width <= imgs[0].width
        imgs=[im.crop((left,0,left+width,im.height)) for im in imgs]
    size=imgs[0].size
    assert all(x.size==size for x in imgs), "Source render sizes must match"
    frames=[]
    for i,im in enumerate(imgs):
        frames.extend([caption(im,labels[i],colour)] * 34)
        for k in range(1,9):
            blended=Image.blend(im,imgs[(i+1)%len(imgs)],k/9)
            frames.append(caption(blended,labels[(i+1)%len(imgs)],colour))
    return frames

def encode(frames,out,stem):
    out.mkdir(parents=True,exist_ok=True)
    width=frames[0].width-(frames[0].width%2)
    height=frames[0].height-(frames[0].height%2)
    mp4=out/(stem+".mp4")
    cmd=["ffmpeg","-y","-v","error","-f","rawvideo","-pix_fmt","rgb24",
         "-s",f"{width}x{height}","-r",str(FPS),"-i","pipe:0",
         "-c:v","libx264","-threads","4","-preset","medium","-crf","19",
         "-pix_fmt","yuv420p","-movflags","+faststart",str(mp4)]
    proc=subprocess.Popen(cmd,stdin=subprocess.PIPE)
    try:
        for im in frames:
            proc.stdin.write(im.crop((0,0,width,height)).tobytes())
    finally:
        proc.stdin.close()
    if proc.wait():
        raise RuntimeError("ffmpeg video encode failed")
    with tempfile.TemporaryDirectory(prefix=stem+"_",dir=out) as td:
        pal=Path(td)/"palette.png"
        subprocess.run(["ffmpeg","-y","-v","error","-filter_threads","1",
                        "-i",str(mp4),"-vf","scale=900:-2:flags=lanczos,palettegen=stats_mode=diff",
                        "-frames:v","1",str(pal)],check=True)
        subprocess.run(["ffmpeg","-y","-v","error","-filter_complex_threads","1",
                        "-i",str(mp4),"-i",str(pal),
                        "-lavfi","scale=900:-2:flags=lanczos[x];[x][1:v]paletteuse=dither=sierra2_4a",
                        "-loop","0",str(out/(stem+".gif"))],check=True)
    frames[0].save(out/(stem+"_poster.png"),compress_level=1)
    print(f"{stem}: {len(frames)/FPS:.2f} s",flush=True)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source-dir",type=Path,required=True)
    ap.add_argument("--out-dir",type=Path,required=True)
    ap.add_argument("--kind",choices=["cooling","viz"],required=True)
    a=ap.parse_args()
    if a.kind=="cooling":
        files=["demo_baseline.png","demo_cooled.png"]
        labels=["Supply air 20 °C · Baseline","Supply air 14 °C · Cooling"]
        colour=(255,217,83)
        stem="cooling_en"
    else:
        files=["ov_geometry.png","ov_temp.png","ov_velocity.png","ov_roi.png","ov_streamlines.png"]
        labels=["Geometry · OpenUSD + RTX","Temperature field · NVIDIA IndeX",
                "Velocity field","Region of interest","GPU streamlines · RK4"]
        colour=(150,230,90)
        stem="visualisations_en"
    encode(sequence([a.source_dir/f for f in files],labels,colour,900/574 if a.kind=="viz" else None),a.out_dir,stem)

if __name__=="__main__":
    main()

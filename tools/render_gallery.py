"""Render tessellated CAD geometry using Pillow; no invented joint imagery."""
import json
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import numpy as np

root = Path(__file__).resolve().parents[1]
items = json.loads((root / "build" / "gallery.json").read_text(encoding="utf-8"))
scale = 2
width, height = 1800, 1000
image = Image.new("RGB", (width*scale, height*scale), "#f3f6fa")
draw = ImageDraw.Draw(image)
font_path = "C:/Windows/Fonts/segoeui.ttf"
font = ImageFont.truetype(font_path, 26*scale)
small = ImageFont.truetype(font_path, 17*scale)
title = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 38*scale)
draw.text((35*scale, 22*scale), "Joint Splitter", fill="#20364b", font=title)
draw.text((35*scale, 76*scale), "Actual FreeCAD geometry  /  0.20 mm clearance  /  Both mating surfaces shown", fill="#5e7185", font=small)

def project(v):
    x, y, z = v
    return (x*0.86+y*0.50, x*0.28-y*0.48-z*0.84, x*0.42-y*0.73+z*0.55)

for index, item in enumerate(items):
    draw = ImageDraw.Draw(image)
    col, row = index % 4, index // 4
    left, top = 24+col*444, 124+row*428
    draw.rounded_rectangle((left*scale, top*scale, (left+428)*scale, (top+410)*scale), radius=14*scale, fill="white")
    draw.text(((left+20)*scale, (top+18)*scale), item["name"], fill="#20364b", font=font)
    meshes = [[project(v) for v in part["vertices"]] for part in item["parts"]]
    all_points = [v for points in meshes for v in points]
    xmin, xmax = min(v[0] for v in all_points), max(v[0] for v in all_points)
    ymin, ymax = min(v[1] for v in all_points), max(v[1] for v in all_points)
    zoom = min(382/(xmax-xmin), 264/(ymax-ymin))
    tris = []
    for pindex, part in enumerate(item["parts"]):
        for triangle in part["triangles"]:
            raw = [part["vertices"][i] for i in triangle]
            a = [raw[1][j]-raw[0][j] for j in range(3)]
            b = [raw[2][j]-raw[0][j] for j in range(3)]
            n = (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
            norm = math.sqrt(sum(x*x for x in n)) or 1
            light = 0.63+0.32*abs(sum(n[j]*(-0.3, -0.4, 0.866)[j] for j in range(3))/norm)
            base = (66, 148, 200) if pindex == 0 else (240, 172, 78)
            color = tuple(round(c*light) for c in base)
            coords = [meshes[pindex][i] for i in triangle]
            points = [((left+214+(v[0]-(xmin+xmax)/2)*zoom)*scale,
                       (top+218+(v[1]-(ymin+ymax)/2)*zoom)*scale) for v in coords]
            tris.append((points, [v[2] for v in coords], color))
    # Per-pixel depth avoids false ridges from intersecting projected triangles.
    ox, oy = left*scale, top*scale
    tile = np.asarray(image.crop((ox, oy, ox+428*scale, oy+410*scale))).copy()
    depth = np.full(tile.shape[:2], -np.inf)
    for points, zs, color in tris:
        pts = [(x-ox, y-oy) for x, y in points]
        (x0,y0),(x1,y1),(x2,y2) = pts
        denominator = (y1-y2)*(x0-x2)+(x2-x1)*(y0-y2)
        if abs(denominator) < 1e-10:
            continue
        bx0 = max(0, int(math.floor(min(x for x,y in pts))))
        by0 = max(0, int(math.floor(min(y for x,y in pts))))
        bx1 = min(tile.shape[1], int(math.ceil(max(x for x,y in pts)))+1)
        by1 = min(tile.shape[0], int(math.ceil(max(y for x,y in pts)))+1)
        if bx1 <= bx0 or by1 <= by0:
            continue
        yy, xx = np.mgrid[by0:by1, bx0:bx1]
        xx, yy = xx+0.5, yy+0.5
        w0 = ((y1-y2)*(xx-x2)+(x2-x1)*(yy-y2))/denominator
        w1 = ((y2-y0)*(xx-x2)+(x0-x2)*(yy-y2))/denominator
        w2 = 1-w0-w1
        zz = w0*zs[0]+w1*zs[1]+w2*zs[2]
        region = depth[by0:by1, bx0:bx1]
        visible = (w0>=-1e-8)&(w1>=-1e-8)&(w2>=-1e-8)&(zz>region)
        region[visible] = zz[visible]
        tile[by0:by1, bx0:bx1][visible] = color
    image.paste(Image.fromarray(tile), (ox, oy))
    draw = ImageDraw.Draw(image)
    draw.text(((left+25)*scale, (top+367)*scale), "Part 1", fill="#35769f", font=small)
    draw.text(((left+309)*scale, (top+367)*scale), "Part 2", fill="#9e6a25", font=small)
draw = ImageDraw.Draw(image)
for index, item in enumerate(items):
    left, top = 24+(index%4)*444, 124+(index//4)*428
    draw.text(((left+20)*scale, (top+18)*scale), item["name"], fill="#20364b", font=font)
    draw.text(((left+25)*scale, (top+367)*scale), "Part 1", fill="#35769f", font=small)
    draw.text(((left+309)*scale, (top+367)*scale), "Part 2", fill="#9e6a25", font=small)
draw.text((35*scale, 22*scale), "Joint Splitter", fill="#20364b", font=title)
draw.text((35*scale, 76*scale), "Actual FreeCAD geometry  /  0.20 mm clearance  /  Both mating surfaces shown", fill="#5e7185", font=small)
image.resize((width, height), Image.Resampling.LANCZOS).save(root / "docs" / "joints.png")
print(root / "docs" / "joints.png")

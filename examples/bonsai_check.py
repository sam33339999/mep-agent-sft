"""Open an exported IFC in Bonsai (the BlenderBIM successor) inside headless Blender and check it.

Verified with: Blender 5.0.1 (pip `bpy`, Python 3.11) + Bonsai 0.8.4 (linux-x64 extension zip).
Prints each mesh's world-space bounding box and volume, and renders a Workbench PNG.

Setup (a one-off, in a separate Python 3.11 venv -- not part of this project's dependencies):
  uv venv bb --python 3.11 && uv pip install --python bb/bin/python bpy "setuptools<81"
  # download bonsai_py311-0.8.4-linux-x64.zip from the IfcOpenShell GitHub release `bonsai-0.8.4`
  bb/bin/python -c "import bpy; bpy.ops.extensions.package_install_files(
      filepath='bonsai_py311-0.8.4-linux-x64.zip', repo='user_default')"
  # pip-bpy does not unpack the extension's wheels, so unzip ~/.config/blender/5.0/extensions/
  # user_default/bonsai/wheels/*.whl into a folder and point SITE below at it.
  # Two headless-only workarounds in that unpacked copy (real Blender does not need them, because
  # bpy.context is "restricted" while an add-on registers):
  #   bonsai/tool/blender.py  get_user_data_dir(): fall back to a temp dir when the add-on
  #                           preferences class is not registered yet
  #   bonsai/bim/schema.py    load_pset_templates(): also return early while `tool.Project` is missing
Run:  bb/bin/python examples/bonsai_check.py out/opening.ifc out/opening.png
Not a GUI session, but it is Bonsai's own `bim.load_project` operator reading the file.
"""
import os
import sys

import bmesh
import bpy
from mathutils import Vector

SITE = os.environ.get("BONSAI_SITE", "bonsai_site")
sys.path.insert(0, SITE)
import addon_utils

ifc, png = sys.argv[-2], sys.argv[-1]
addon_utils.enable("bl_ext.user_default.bonsai", default_set=True, persistent=True)
print("load_project ->", bpy.ops.bim.load_project(filepath=ifc), flush=True)

def world_bbox(o):
    pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
    return [round(min(p[i] for p in pts), 3) for i in range(3)], [round(max(p[i] for p in pts), 3) for i in range(3)]

def volume(o):
    bm = bmesh.new(); bm.from_mesh(o.data); bmesh.ops.triangulate(bm, faces=bm.faces[:])
    vol = 0.0
    for f in bm.faces:
        a, b, c = [o.matrix_world @ vv.co for vv in f.verts]
        vol += a.dot(b.cross(c)) / 6.0
    bm.free(); return abs(vol)

print("RESULT objects:")
for o in bpy.data.objects:
    if o.type == "MESH":
        lo, hi = world_bbox(o)
        print(f"  {o.name:28s} bbox lo={lo} hi={hi} volume={volume(o):.3f} m3 verts={len(o.data.vertices)}", flush=True)

# visual evidence: a Workbench render from a 3/4 view
sc = bpy.context.scene
for o in list(bpy.data.objects):
    if o.type in ("CAMERA", "LIGHT") or o.name == "Cube":
        bpy.data.objects.remove(o)
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
pts = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
lo = Vector([min(p[i] for p in pts) for i in range(3)]); hi = Vector([max(p[i] for p in pts) for i in range(3)])
ctr, size = (lo + hi) / 2, (hi - lo).length
cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam")); sc.collection.objects.link(cam)
cam.location = ctr + Vector((0.8, -1.1, 0.7)) * size
direction = ctr - cam.location
cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
sc.camera = cam
sc.render.engine = "BLENDER_WORKBENCH"
sc.render.resolution_x, sc.render.resolution_y = 1200, 760
sc.render.filepath = png
sc.display.shading.light = "STUDIO"; sc.display.shading.color_type = "MATERIAL"
try:
    bpy.ops.render.render(write_still=True)
    print("rendered:", png, flush=True)
except Exception as e:
    print("render failed:", type(e).__name__, str(e)[:200], flush=True)

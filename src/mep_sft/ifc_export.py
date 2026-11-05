"""Optional IFC4 export (needs `ifcopenshell`: `uv sync --extra ifc`).

Walls become IfcWall with IfcOpeningElement voids; each straight pipe run becomes an
IfcPipeSegment (extruded circle). Open the result in BlenderBIM or any IFC viewer.
"""

from __future__ import annotations

import math

from .scene import Scene


def _basis(origin, x, y, z):
    import numpy as np

    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, y, z, origin
    return m


def export_ifc(scene: Scene, path: str) -> None:
    import ifcopenshell
    from ifcopenshell import api

    f = api.run("project.create_file", version="IFC4")
    project = api.run("root.create_entity", f, ifc_class="IfcProject", name="MEP SFT scene")
    api.run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})  # scenes are in metres
    model = api.run("context.add_context", f, context_type="Model")
    body = api.run("context.add_context", f, context_type="Model", context_identifier="Body",
                   target_view="MODEL_VIEW", parent=model)
    site = api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    building = api.run("root.create_entity", f, ifc_class="IfcBuilding", name="Building")
    storey = api.run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="Level 0")
    api.run("aggregate.assign_object", f, relating_object=project, products=[site])
    api.run("aggregate.assign_object", f, relating_object=site, products=[building])
    api.run("aggregate.assign_object", f, relating_object=building, products=[storey])

    for w in scene.walls:
        dx, dy = w.p1[0] - w.p0[0], w.p1[1] - w.p0[1]
        length = math.hypot(dx, dy)
        ux, uy = dx / length, dy / length
        wall = api.run("root.create_entity", f, ifc_class="IfcWall", name=w.id)
        rep = api.run("geometry.add_wall_representation", f, context=body, length=length,
                      height=w.height, thickness=w.thickness)
        api.run("geometry.assign_representation", f, product=wall, representation=rep)
        # the representation extrudes thickness along local +y: shift back by half to centre it
        origin = (w.p0[0] + uy * w.thickness / 2, w.p0[1] - ux * w.thickness / 2, w.z0)
        api.run("geometry.edit_object_placement", f, product=wall,
                matrix=_basis(origin, (ux, uy, 0), (-uy, ux, 0), (0, 0, 1)))
        api.run("spatial.assign_container", f, relating_structure=storey, products=[wall])
        for k, o in enumerate(w.openings):
            op = api.run("root.create_entity", f, ifc_class="IfcOpeningElement",
                         name=f"{w.id}-opening-{k + 1}")
            orep = api.run("geometry.add_wall_representation", f, context=body,
                           length=o.u1 - o.u0, height=o.z1 - o.z0, thickness=w.thickness * 1.5)
            api.run("geometry.assign_representation", f, product=op, representation=orep)
            oo = (w.p0[0] + ux * o.u0 + uy * w.thickness * 0.75,
                  w.p0[1] + uy * o.u0 - ux * w.thickness * 0.75, o.z0)
            api.run("geometry.edit_object_placement", f, product=op,
                    matrix=_basis(oo, (ux, uy, 0), (-uy, ux, 0), (0, 0, 1)))
            api.run("feature.add_feature", f, feature=op, element=wall)

    for p in scene.pipes:
        for i, (a, b) in enumerate(zip(p.points, p.points[1:], strict=False)):
            d = [b[k] - a[k] for k in range(3)]
            length = math.sqrt(sum(c * c for c in d))
            if length < 1e-6:
                continue
            z = [c / length for c in d]
            helper = (0, 0, 1) if abs(z[2]) < 0.9 else (1, 0, 0)
            x = [z[1] * helper[2] - z[2] * helper[1], z[2] * helper[0] - z[0] * helper[2],
                 z[0] * helper[1] - z[1] * helper[0]]
            xn = math.sqrt(sum(c * c for c in x))
            x = [c / xn for c in x]
            y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]]
            seg = api.run("root.create_entity", f, ifc_class="IfcPipeSegment",
                          name=f"{p.id}-{i + 1}")
            seg.Description = f"{p.system} Ø{p.diameter} m"
            profile = f.create_entity("IfcCircleProfileDef", ProfileType="AREA",
                                      ProfileName=p.id, Radius=p.diameter / 2)
            rep = api.run("geometry.add_profile_representation", f, context=body, profile=profile,
                          depth=length)
            api.run("geometry.assign_representation", f, product=seg, representation=rep)
            api.run("geometry.edit_object_placement", f, product=seg,
                    matrix=_basis(a, x, y, z))
            api.run("spatial.assign_container", f, relating_structure=storey, products=[seg])
    f.write(path)
    ifcopenshell.open(path)  # fail loudly if what we wrote cannot be read back

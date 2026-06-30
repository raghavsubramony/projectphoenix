import bpy
import math
import os

# =====================================================
# CLEAN SCENE
# =====================================================

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

# =====================================================
# PARAMETERS
# =====================================================

NUM_CARTRIDGES = 12
RADIUS = 5.0

# =====================================================
# MATERIALS
# =====================================================

def create_material(name, color, alpha=1.0):
    mat = bpy.data.materials.new(name)

    bsdf = mat.node_tree.nodes["Principled BSDF"]

    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Alpha"].default_value = alpha

    mat.blend_method = 'BLEND'

    return mat

housing_mat = create_material(
    "Housing",
    (0.3, 0.7, 1.0, 1.0),
    0.15
)

cartridge_mat = create_material(
    "Cartridge",
    (0.2, 0.2, 0.2, 1.0),
    1
)

power_mat = create_material(
    "PowerFlow",
    (0.0, 1.0, 0.3, 1.0),
    1
)

fuel_mat = create_material(
    "Fuel",
    (1.0, 0.8, 0.0, 1.0),
    1
)

# =====================================================
# CENTRAL HUB
# =====================================================

bpy.ops.mesh.primitive_cylinder_add(
    radius=1.5,
    depth=1
)

hub = bpy.context.object
hub.name = "CentralHub"

# =====================================================
# TRANSPARENT HOUSING
# =====================================================

bpy.ops.mesh.primitive_uv_sphere_add(
    radius=6.5
)

housing = bpy.context.object
housing.name = "Housing"

housing.scale[2] = 0.45
housing.data.materials.append(housing_mat)

# =====================================================
# CARTRIDGES
# =====================================================

for i in range(NUM_CARTRIDGES):

    angle = math.radians(i * 360 / NUM_CARTRIDGES)

    x = RADIUS * math.cos(angle)
    y = RADIUS * math.sin(angle)

    bpy.ops.mesh.primitive_cylinder_add(
        radius=0.55,
        depth=1.8,
        location=(x, y, 0)
    )

    cartridge = bpy.context.object

    cartridge.rotation_euler[0] = math.radians(90)
    cartridge.rotation_euler[2] = angle

    cartridge.name = f"Cartridge_{i+1}"

    cartridge.data.materials.append(cartridge_mat)

# =====================================================
# FUEL DISTRIBUTION RING
# =====================================================

bpy.ops.curve.primitive_bezier_circle_add(
    radius=4.5
)

fuel_ring = bpy.context.object
fuel_ring.name = "FuelRing"

fuel_ring.data.bevel_depth = 0.05

# =====================================================
# EXHAUST COLLECTOR RING
# =====================================================

bpy.ops.curve.primitive_bezier_circle_add(
    radius=5.7
)

exhaust_ring = bpy.context.object
exhaust_ring.name = "ExhaustRing"

exhaust_ring.data.bevel_depth = 0.08

# =====================================================
# POWER FLOW LINES
# =====================================================

for i in range(NUM_CARTRIDGES):

    angle = math.radians(i * 360 / NUM_CARTRIDGES)

    x = RADIUS * math.cos(angle)
    y = RADIUS * math.sin(angle)

    curve = bpy.data.curves.new(
        f"PowerFlow_{i}",
        'CURVE'
    )

    curve.dimensions = '3D'

    spline = curve.splines.new('POLY')
    spline.points.add(1)

    spline.points[0].co = (x, y, 0, 1)
    spline.points[1].co = (0, 0, 0, 1)

    obj = bpy.data.objects.new(
        f"PowerFlow_{i}",
        curve
    )

    bpy.context.collection.objects.link(obj)

    curve.bevel_depth = 0.03

# =====================================================
# HV DC BUS
# =====================================================

bpy.ops.mesh.primitive_cube_add(
    location=(0, -8, 0)
)

dcbus = bpy.context.object

dcbus.scale = (3, 0.4, 0.3)
dcbus.name = "HV_DC_BUS"

# =====================================================
# SUPERCAPACITOR BANK
# =====================================================

for i in range(6):

    bpy.ops.mesh.primitive_cylinder_add(
        radius=0.3,
        depth=1.2,
        location=(7, i * 0.8 - 2.0, 0)
    )

    cap = bpy.context.object
    cap.name = f"SuperCap_{i+1}"

# =====================================================
# CAMERA
# =====================================================

bpy.ops.object.camera_add(
    location=(0, -15, 7)
)

cam = bpy.context.object
cam.name = "RenderCamera"

cam.rotation_euler = (
    math.radians(65),
    0,
    0
)

bpy.context.scene.camera = cam

# =====================================================
# LIGHTING
# =====================================================

bpy.ops.object.light_add(
    type='AREA',
    location=(0, 0, 10)
)

light = bpy.context.object
light.data.energy = 2500

# =====================================================
# CAMERA ORBIT ANIMATION
# =====================================================

scene = bpy.context.scene
scene.frame_start = 1
scene.frame_end = 360

cam.location = (0, -15, 7)
cam.keyframe_insert(data_path="location", frame=1)

cam.location = (15, 0, 7)
cam.keyframe_insert(data_path="location", frame=120)

cam.location = (0, 15, 7)
cam.keyframe_insert(data_path="location", frame=240)

cam.location = (-15, 0, 7)
cam.keyframe_insert(data_path="location", frame=360)

output_dir = os.path.dirname(os.path.abspath(__file__))
blend_path = os.path.join(output_dir, "phoenix_x12.blend")
render_path = os.path.join(output_dir, "phoenix_x12.png")
gltf_path = os.path.join(output_dir, "phoenix_x12.gltf")

bpy.ops.wm.save_as_mainfile(filepath=blend_path)
bpy.ops.export_scene.gltf(filepath=gltf_path)
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080
scene.render.image_settings.file_format = "PNG"
scene.render.filepath = render_path
bpy.ops.render.render(write_still=True)

print("PHOENIX-X12 concept scene created.")
print(f"Blend file: {blend_path}")
print(f"Render image: {render_path}")
print("Open the .blend in Blender, or view the .png in any image viewer.")
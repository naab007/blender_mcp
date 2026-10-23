# Phase D1 (generic driver) headless steps. Executed inside tests/headless_handlers.py when it is run with
# `-- --drv`; uses that file's helpers (step, call, ok, err, clear_scene, WORK, addon, srv, HANDLERS, P, A,
# snapshot). Written from docs/PLAN.md section 12 (contract D1.0 + rulings R1-R21) and docs/TEST-PLAN-C.md;
# the expected error substrings are the DRVCHECK texts recorded in docs/apichecks/out_*.jsonl (R21: RNA texts
# pass through verbatim). Fixtures are built with bpy.data / bpy.ops primitives, never with the handler under
# test; uid readback is bpy's own `session_uid`.
# Driver handlers never raise (12.2.2): a `status: error` reply from execute_command is a FAIL for every driver
# step, so the helpers below are stricter than ok()/err(): dcall asserts status success, dok asserts no
# `error` key, derr asserts an `error` key on a status-success reply.
# Landing map (12.3): steps DRV-1..4c and DRV-11..12d need Linus L1; DRV-5..9 need L2/L3 (T2, added between
# the markers below); DRV-10 needs L4.
import json
import math
import time

import bpy

DRVDIR = __import__("os").path.join(WORK, "drv")
__import__("os").makedirs(DRVDIR, exist_ok=True)


def dcall(cmd, **params):
    r = call(cmd, **params)
    assert isinstance(r, dict), "reply is not a dict: %r" % (r,)
    assert r.get("status") == "success", "driver handler raised or is missing (status=%s): %s" % (r.get("status"), A(r.get("message")))
    res = r.get("result")
    assert isinstance(res, dict), "driver reply is not a dict: %s" % A(json.dumps(res)[:200])
    return res


def dok(cmd, must=None, **params):
    res = dcall(cmd, **params)
    assert "error" not in res, "%s returned error: %s" % (cmd, A(json.dumps(res)[:400]))
    assert res.get("success") is True, "%s reply lacks success:true: %s" % (cmd, A(json.dumps(res)[:200]))
    for k in (must or []):
        assert k in res, "%s reply lacks key %r: %s" % (cmd, k, A(json.dumps(res)[:300]))
    return res


def derr(cmd, contains=None, **params):
    res = dcall(cmd, **params)
    assert "error" in res, "%s expected an error, got %s" % (cmd, A(json.dumps(res)[:300]))
    msg = str(res["error"])
    assert "Traceback" not in msg, "driver error is a traceback: %s" % A(msg[:200])
    if contains:
        assert contains.lower() in msg.lower(), "error %r lacks %r" % (A(msg), contains)
    return res


def _enc_ok(value, path="reply"):
    """No bpy structs anywhere in a reply (the K5 rule of B1, kept for the driver)."""
    if isinstance(value, dict):
        for k, v in value.items():
            _enc_ok(v, "%s.%s" % (path, k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _enc_ok(v, "%s[%d]" % (path, i))
    else:
        assert value is None or isinstance(value, (str, int, float, bool)), "%s carries %r" % (path, type(value))


def _uid(name):
    o = bpy.data.objects.get(name)
    assert o is not None, "fixture object %s missing" % name
    return o.session_uid


clear_scene()
bpy.context.scene.frame_set(1)
U = {}   # uids shared across steps: mesh, ob, parent


# ---- doc step 1: describe
def d1():
    # R32: a full Object descriptor is ~39k chars > the 24k default cap; the cap drops description strings FIRST and
    # says so in `truncated`; whole properties are never dropped (or nulled) at the default cap
    r = dok("drv_describe", must=["kind", "identifier", "properties", "chars", "truncated"], what="Object")
    assert r["kind"] == "struct" and r["identifier"] == "Object" and r.get("base") == "ID", (r["kind"], r["identifier"], r.get("base"))
    props = r["properties"]
    # R32b: a full Object descriptor is ~39k chars, so the 24k default always drops a trailing ~25-30 props; the
    # reply's own count is the number of KEPT entries and the dropped tail is named compactly in `truncated`
    assert len(props) >= 100 and r["count"] == len(props), (r["count"], len(props))
    nulls = [k for k, v in props.items() if not isinstance(v, dict)]
    assert not nulls, "properties nulled at the default cap: %s" % nulls[:10]
    assert len(json.dumps(r)) <= 24000 + 400, len(json.dumps(r))
    loc = props["location"]
    assert loc["type"] == "FLOAT" and loc["array_length"] == 3 and loc["unit"] == "LENGTH" and loc["readonly"] is False, loc
    assert loc["subtype"] == "TRANSLATION" and loc["default"] == [0.0, 0.0, 0.0], loc
    ty = props["type"]
    assert ty["type"] == "ENUM" and ty["readonly"] is True, ty
    ids = [i[0] for i in ty["enum_items"]]
    assert "MESH" in ids and "ARMATURE" in ids and all(len(i) == 3 for i in ty["enum_items"]), ids
    assert props["parent"]["type"] == "POINTER" and props["parent"]["fixed_type"] == "Object", props["parent"]
    assert props["modifiers"]["type"] == "COLLECTION", props["modifiers"]
    assert "functions" not in r, "functions must be opt-in"
    assert r["truncated"] and "description" in str(r["truncated"][0]).lower(), "R32: truncated must name descriptions FIRST: %s" % A(r["truncated"][:3])
    assert all(len(t) < 400 for t in map(str, r["truncated"])), "R34: compact truncated entries: %s" % A([len(str(t)) for t in r["truncated"]])
    assert isinstance(r["chars"], int) and 1000 < r["chars"] <= 24000, r["chars"]
    big = dok("drv_describe", what="Object", max_chars=60000)
    assert big["truncated"] == [] and big["count"] >= 130 and len(big["properties"]) >= 130, (big["truncated"][:2], big["count"])
    assert big["properties"]["location"]["description"] == "Location of the object", big["properties"]["location"].get("description")
    assert ["MESH", "Mesh", ""] in big["properties"]["type"]["enum_items"], big["properties"]["type"]["enum_items"][:2]
    # a property ref describes that one property (oracle step 1)
    rp = dok("drv_describe", what="SCENE:Scene/render.engine")
    assert rp["kind"] == "property" and rp["type"] == "ENUM" and rp["dynamic"] is True, A(rp)
    ro = derr("drv_describe", "Objekt", what="Objekt")
    assert "Object" in ro.get("closest", []), A(ro)
    _enc_ok(r)
    # fields filter
    rf = dok("drv_describe", what="Object", fields="location,name")
    assert set(rf["properties"]) == {"location", "name"}, list(rf["properties"])
    # functions
    rfn = dok("drv_describe", what="Object", include_functions=True, fields="name")
    ev = rfn["functions"]["evaluated_get"]
    assert ["depsgraph", "POINTER", False, True] in ev["params"], ev
    # search
    rs = dok("drv_describe", what="Object", search="rotation")
    assert "rotation_euler" in rs["properties"] and "location" not in rs["properties"], list(rs["properties"])[:10]
    for k, v in rs["properties"].items():
        assert "rotation" in (k + str(v.get("description", ""))).lower(), k
    # catalogue cache: byte-identical second read (uncapped, so the comparison covers the descriptions too)
    r2 = dok("drv_describe", what="Object", max_chars=60000)
    assert json.dumps(big["properties"], sort_keys=True) == json.dumps(r2["properties"], sort_keys=True), "describe not stable"
    # a ref as `what` (request doc 2.2): the struct it resolves to
    rr = dok("drv_describe", what="SCENE:Scene/render")
    assert rr["identifier"] == "RenderSettings", rr["identifier"]
    assert "dynamic" in rr["properties"]["engine"] and isinstance(rr["properties"]["engine"]["dynamic"], bool), rr["properties"]["engine"]
    return "Object %d props (location FLOAT[3] LENGTH, type ENUM ro, parent->Object, modifiers COLLECTION), fields/search/functions, cached, ref->RenderSettings" % len(props)


step("DRV-1 describe(Object) struct", d1, readonly=True, tag="DRV")


def d1b():
    r = dok("drv_describe", must=["kind", "idname", "properties", "poll_now", "refused"], what="mesh.bevel")
    assert r["kind"] == "operator" and r["idname"] == "mesh.bevel" and r["name"] == "Bevel", (r["kind"], r["idname"], r.get("name"))
    off = r["properties"]["offset"]
    assert off["type"] == "FLOAT" and off["soft_min"] == 0.0 and off["soft_max"] == 100.0, off
    assert r["properties"]["offset_type"]["type"] == "ENUM" and r["properties"]["segments"]["type"] == "INT"
    assert r["poll_now"] is False, "mesh.bevel must not poll in object mode"
    assert r["refused"] is False and "bl_options" not in r, "R19: bl_options dropped, refused false"
    rq = dok("drv_describe", what="wm.quit_blender")
    assert rq["refused"] is True, rq
    ro = dok("drv_describe", must=["count", "operators"], what="ops", search="bevel")
    assert ro["kind"] == "ops" and ro["count"] >= 1 and any(o[0] == "mesh.bevel" for o in ro["operators"]), ro["count"]
    assert all(len(o) == 2 for o in ro["operators"]), ro["operators"][:2]
    e1 = derr("drv_describe", "NoSuchStruct", what="NoSuchStruct")
    assert isinstance(e1.get("closest"), list) and 0 < len(e1["closest"]) <= 3, e1
    e2 = derr("drv_describe", "mesh.no_such_op", what="mesh.no_such_op")
    assert isinstance(e2.get("closest"), list) and e2["closest"], e2
    derr("drv_describe", what="")
    _enc_ok(r)
    return "mesh.bevel: offset FLOAT soft 0..100, poll_now False, no bl_options; quit_blender refused; ops search; closest on misses"


step("DRV-1b describe(mesh.bevel) + ops + misses", d1b, readonly=True, tag="DRV")


# ---- doc step 2 (L1 part): data_new twice with a ref for object_data, find, uids in the friendly views.
# The `batch` + `$` substitution form of the doc's step 2 is DRV-2b (after Linus L3).
def d2():
    m = dok("drv_new", must=["uid", "id_type", "name", "linked_to"], collection="meshes", args=["DrvMesh"])
    assert m["id_type"] == "MESH" and m["name"] == "DrvMesh" and m["linked_to"] is None and isinstance(m["uid"], int), m
    # fixture geometry via bpy (never the handler under test): a closed cube, so mesh.bevel in DRV-5 has faces to work on
    # (Linus T-A: bevel returns CANCELLED on an empty mesh; a single quad bevels FINISHED but adds no polygons)
    V = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
    F = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    bpy.data.meshes["DrvMesh"].from_pydata(V, [], F)
    bpy.data.meshes["DrvMesh"].update()
    assert len(bpy.data.meshes["DrvMesh"].polygons) == 6
    o = dok("drv_new", must=["uid", "id_type", "name", "linked_to"], collection="objects", args=["Drv", "#%d" % m["uid"]], link_to="scene")
    assert o["id_type"] == "OBJECT" and o["name"] == "Drv" and o["uid"] != m["uid"], o
    assert o["linked_to"], "object must be linked somewhere by default"
    ob = bpy.data.objects["Drv"]
    assert ob.data.name == "DrvMesh" and ob.session_uid == o["uid"] and bpy.data.meshes["DrvMesh"].session_uid == m["uid"]
    assert ob.name in bpy.context.scene.collection.objects, "not linked into the scene root collection"
    U["mesh"], U["ob"] = m["uid"], o["uid"]
    f = dok("drv_find", must=["count", "total", "items"], name="Drv*")
    assert f["count"] == 1 and f["items"][0]["uid"] == o["uid"] and f["items"][0]["name"] == "Drv" and f["items"][0]["type"] == "MESH", f
    fm = dok("drv_find", id_type="MESH", name="DrvMesh")
    assert fm["count"] == 1 and fm["items"][0]["uid"] == m["uid"] and fm.get("scope") == "DATA", fm
    fw = dok("drv_find", where=[["type", "==", "MESH"], ["name", "contains", "Drv"]], fields="name,type,location")
    assert fw["count"] == 1 and fw["items"][0]["uid"] == o["uid"] and fw["items"][0]["location"] == [0.0, 0.0, 0.0], fw
    ew = derr("drv_new", "window_managers", collection="window_managers", args=["x"])
    assert "objects" in ew.get("valid", []), A(ew)
    fw2 = dok("drv_find", where=[["modifiers[].type", "contains", "ARMATURE"]])
    assert fw2["count"] == 0, fw2
    eb = derr("drv_find", where=[["type", "~=", "MESH"]])
    assert isinstance(eb.get("valid"), list) and "==" in eb["valid"], eb
    # G11 (a): uids in the friendly views, equal to session_uid
    si = ok(call("get_scene_info"))
    ent = [x for x in si["objects"] if x["name"] == "Drv"]
    assert ent and ent[0].get("uid") == o["uid"], "get_scene_info lacks uid: %s" % A(ent[:1])
    oi = ok(call("get_object_info", name="Drv"))
    assert oi.get("uid") == o["uid"], "get_object_info lacks uid: %s" % A(list(oi)[:12])
    ft = ok(call("find_objects_by_type", obj_type="MESH"))
    lst = ft.get("objects", ft if isinstance(ft, list) else [])
    hit = [x for x in lst if isinstance(x, dict) and x.get("name") == "Drv"]
    assert hit and hit[0].get("uid") == o["uid"], "find_objects_by_type lacks uid: %s" % A(lst[:1])
    # misses
    e1 = derr("drv_new", "no_such", collection="no_such")
    assert isinstance(e1.get("valid"), list) and "objects" in e1["valid"] and "meshes" in e1["valid"], e1
    e2 = derr("drv_new", "screens", collection="screens")
    assert "objects" in e2.get("valid", []), e2
    # Blender's dedupe is reported, not the requested name
    d = dok("drv_new", collection="objects", args=["Drv", None])
    assert d["name"] == "Drv.001", d["name"]
    bpy.data.objects.remove(bpy.data.objects["Drv.001"], do_unlink=True)
    # initial `set` on the new ID (paths relative to it)
    mat = dok("drv_new", must=["set"], collection="materials", args=["DrvMat"], set={"diffuse_color": [1, 0, 0, 1]})
    assert mat["set"]["applied"] == 1 and mat["set"]["failed"] == 0, mat["set"]
    assert tuple(round(c, 3) for c in bpy.data.materials["DrvMat"].diffuse_color) == (1.0, 0.0, 0.0, 1.0)
    _enc_ok(o); _enc_ok(f)
    return "meshes.new + objects.new(#uid) linked, uids %d/%d, find by glob/where, uids in 3 friendly views, valid-list errors, dedupe Drv.001, set on new material" % (m["uid"], o["uid"])


step("DRV-2 data_new + find + uids in friendly views", d2, tag="DRV")


# ---- doc step 3: rna_set / rna_get, angles, coercion, stop_on_error
def d3():
    u = U["ob"]
    ob = bpy.data.objects["Drv"]
    r = dok("drv_set", must=["results", "applied", "failed", "stopped_at"],
            sets=[["#%d/location" % u, [1, 2, 3]], ["#%d/rotation_euler" % u, [90, 0, 0]]], angles="DEGREES")
    assert r["applied"] == 2 and r["failed"] == 0 and r["stopped_at"] is None, r
    assert all(x["set"] is True and "value_after" in x for x in r["results"]), r["results"]
    assert r["results"][0]["value_after"] == [1.0, 2.0, 3.0], r["results"][0]
    # R23: the default field set = scalars + pointers as ref dicts, no collections
    gd0 = dok("drv_get", refs=["#%d" % u])
    v0 = gd0["results"][0]["value"]
    assert "location" in v0 and v0["parent"] is None and "modifiers" not in v0, A(list(v0)[:30])
    assert v0["data"] == {"uid": U["mesh"], "id_type": "MESH", "name": "DrvMesh"}, A(v0.get("data"))
    assert tuple(ob.location) == (1.0, 2.0, 3.0) and abs(ob.rotation_euler.x - math.pi / 2) < 1e-4, (tuple(ob.location), ob.rotation_euler.x)
    g = dok("drv_get", must=["results", "errors", "chars", "truncated"], refs=["#%d" % u], fields="location,rotation_euler")
    res = g["results"][0]
    assert res["ref"] == "#%d" % u and res["uid"] == u and res["id_type"] == "OBJECT" and res["name"] == "Drv", res
    assert res["value"]["location"] == [1.0, 2.0, 3.0] and abs(res["value"]["rotation_euler"][0] - 1.5708) < 1e-3, res["value"]
    assert g["errors"] == [] and g["truncated"] == [] and isinstance(g["chars"], int), g
    gd = dok("drv_get", refs=["#%d" % u], fields="rotation_euler", angles="DEGREES")
    assert abs(gd["results"][0]["value"]["rotation_euler"][0] - 90.0) < 1e-3, gd["results"][0]["value"]
    # four items, stop_on_error=False: read-only, bad enum, unknown prop, then a valid one that must still apply
    engine_before = bpy.context.scene.render.engine
    bad = [["#%d/type" % u, "EMPTY"], ["SCENE:Scene/render.engine", "NOPE"], ["#%d/no_such_prop" % u, 1], ["#%d/location[2]" % u, 9]]
    r4 = dcall("drv_set", sets=bad, stop_on_error=False)
    assert r4["success"] is False and r4["applied"] == 1 and r4["failed"] == 3, (r4["success"], r4["applied"], r4["failed"])
    i0, i1, i2, i3 = r4["results"]
    assert i0["set"] is False and "read-only" in i0.get("rna_error", i0["error"]).lower(), i0   # R21: RNA text verbatim
    assert i1["set"] is False and "NOPE" in i1["error"] and "CYCLES" in i1.get("valid", []) and "BLENDER_WORKBENCH" in i1["valid"], i1
    assert i2["set"] is False and isinstance(i2.get("closest"), list), i2
    assert i3["set"] is True and ob.location.z == 9.0 and bpy.context.scene.render.engine == engine_before, (i3, ob.location.z)
    # stop_on_error=True: stops at the first failure, the valid last item never applies
    bad[3][1] = 11
    r5 = dcall("drv_set", sets=bad)
    assert r5["stopped_at"] == 0 and r5["applied"] == 0 and r5["failed"] == 1 and ob.location.z == 9.0, (r5, ob.location.z)
    # coercion table (12.2.4), Blender truth after each
    par = bpy.data.objects.new("DrvParent", None)
    bpy.context.scene.collection.objects.link(par)
    U["parent"] = par.session_uid
    c = dok("drv_set", sets=[["#%d/scale[0]" % u, 2], ["#%d/hide_render" % u, 1], ["#%d/rotation_mode" % u, "XYZ"],
                             ["MATERIAL:DrvMat/diffuse_color", [0, 1, 0, 1]], ["#%d/parent" % u, "#%d" % par.session_uid]])
    assert c["applied"] == 5, c
    assert ob.scale.x == 2.0 and ob.hide_render is True and ob.rotation_mode == "XYZ" and ob.parent is par
    assert tuple(round(x, 3) for x in bpy.data.materials["DrvMat"].diffuse_color) == (0.0, 1.0, 0.0, 1.0)
    c2 = dok("drv_set", sets={"#%d/parent" % u: {"uid": par.session_uid}})
    assert c2["applied"] == 1 and ob.parent is par, "pointer by {uid} dict"
    c3 = dok("drv_set", sets=[["#%d/parent" % u, "OBJECT:DrvParent"]])
    assert c3["applied"] == 1 and ob.parent is par, "pointer by IDTYPE:Name"
    c4 = dok("drv_set", sets=[["#%d/parent" % u, None]])
    assert c4["applied"] == 1 and ob.parent is None, "null clears a pointer"
    c5 = dok("drv_set", sets=[['#%d/["drv_tag"]' % u, "x"]])
    assert c5["applied"] == 1 and ob["drv_tag"] == "x", "ID-property path"
    gi = dok("drv_get", refs=['#%d/["drv_tag"]' % u])
    assert gi["results"][0]["value"] == "x", gi["results"]
    gc = dok("drv_get", refs=["#%d" % u], fields="custom")
    assert gc["results"][0]["value"]["custom"]["drv_tag"] == "x", A(gc["results"][0]["value"])
    fired = []
    h = lambda scene, *a: fired.append(scene.frame_current)
    bpy.app.handlers.frame_change_post.append(h)
    try:
        c6 = dok("drv_set", sets=[["SCENE:Scene/frame_current", 5]])
    finally:
        bpy.app.handlers.frame_change_post.remove(h)
    assert c6["applied"] == 1 and bpy.context.scene.frame_current == 5 and fired == [5], "frame_current must go through frame_set: %s" % fired
    bpy.context.scene.frame_set(1)
    # value encoding on the way out
    ob.modifiers.new("DrvSub", "SUBSURF")
    try:
        ge = dok("drv_get", refs=["#%d" % u], fields="matrix_world,parent,modifiers,type,hide_render,name")
        v = ge["results"][0]["value"]
        assert len(v["matrix_world"]) == 4 and all(len(row) == 4 for row in v["matrix_world"]), v["matrix_world"]
        assert v["parent"] is None and v["modifiers"] == {"count": 1} and v["type"] == "MESH" and v["hide_render"] is True, v
        gm = dok("drv_get", refs=["#%d" % u], fields="modifiers[]", depth=1)
        items = gm["results"][0]["value"]["modifiers"]["items"]
        assert items[0]["name"] == "DrvSub" and items[0]["type"] == "SUBSURF", items
        gp = dok("drv_get", refs=["#%d" % u], fields="parent")
        bpy.data.objects["Drv"].parent = par
        gp2 = dok("drv_get", refs=["#%d" % u], fields="parent")
        assert gp["results"][0]["value"]["parent"] is None and gp2["results"][0]["value"]["parent"] == {"uid": par.session_uid, "id_type": "OBJECT", "name": "DrvParent"}, gp2
        bpy.data.objects["Drv"].parent = None
    finally:
        ob.modifiers.remove(ob.modifiers["DrvSub"])
    # many refs, one round trip; bad refs land in errors, the good ones still read
    gmany = dok("drv_get", refs=["#%d" % u, "OBJECT:Drv", "MESH:DrvMesh", "OBJECT:NoSuch", "#999999", "BOGUS:Cube", "#%d/location[7]" % u, "#%d/nope.nada" % u], fields="name")
    assert [x["uid"] for x in gmany["results"]] == [u, u, U["mesh"]], gmany["results"]
    errs = {e["ref"]: e for e in gmany["errors"]}
    assert len(errs) == 5, list(errs)
    assert "objects" in errs["OBJECT:NoSuch"].get("tried", []), errs["OBJECT:NoSuch"]
    assert "tried" in errs["#999999"], errs["#999999"]
    assert ("closest" in errs["BOGUS:Cube"] or "valid" in errs["BOGUS:Cube"]), errs["BOGUS:Cube"]
    assert "could not be resolved" in errs["#%d/nope.nada" % u]["error"], errs["#%d/nope.nada" % u]
    for e in errs.values():
        assert "Traceback" not in e["error"]
    derr("drv_set", sets="{not json")
    # 12.2.2: a bad ref is never a top-level error for drv_get; it lands in `errors` with the grammar in `valid`
    ge = dok("drv_get", refs=[""])
    assert ge["results"] == [] and ge["errors"][0]["ref"] == "" and "#<uid>" in ge["errors"][0].get("valid", []), A(ge)
    hide_restore = dok("drv_set", sets=[["#%d/hide_render" % u, False], ["#%d/scale" % u, [1, 1, 1]], ["#%d/rotation_euler" % u, [0, 0, 0]]])
    assert hide_restore["applied"] == 3
    _enc_ok(gmany)
    return "DEGREES in -> radians out (1.5708) / 90 with DEGREES; ro+enum+unknown teach with valid/closest, stop_on_error both ways; 9 coercions incl pointer by #uid/{uid}/IDTYPE/null, idprop, frame_set; encoding matrix/pointer/collection; 8 refs one trip, 5 errors with tried"


step("DRV-3 rna_set / rna_get coercion + angles + stop_on_error", d3, tag="DRV")


# ---- oracle "malformed refs": every one an error entry naming the ref, never a traceback, mode OBJECT after
def d3b():
    bad = ["", "#", "#abc", "OBJECT:", "OBJECT:Cube/", "/location", "#1/..//x", "context/", "data/nope", "prefs/nope", "x" * 10000]
    for b in bad:
        g = dok("drv_get", refs=[b])
        assert g["results"] == [] and g["errors"] and g["errors"][0]["ref"] == b, "ref %r: %s" % (A(b[:40]), A(json.dumps(g)[:300]))
        assert "Traceback" not in g["errors"][0]["error"], A(g["errors"][0]["error"][:200])
        assert bpy.context.mode == "OBJECT"
    e = derr("drv_get", "max_chars", refs=["#%d" % U["ob"]], max_chars=0)
    return "%d malformed refs -> errors[].ref echoed, no traceback; max_chars=0 teaches" % len(bad)


step("DRV-3b malformed refs sweep", d3b, readonly=True, tag="DRV")


# ---- doc step 4 (L1 part): rename by uid; the old name is gone, the uid still resolves, the uid cache never goes stale.
# scene_snapshot / scene_diff after the second rename is DRV-4b (after Linus L2).
def d4():
    u = U["ob"]
    r = dok("drv_set", sets=[["#%d/name" % u, "DrvRenamed"]])
    assert r["applied"] == 1 and bpy.data.objects.get("Drv") is None and bpy.data.objects["DrvRenamed"].session_uid == u
    f = dok("drv_find", name="Drv")
    assert f["count"] == 0 and f["items"] == [], f
    g = dok("drv_get", refs=["OBJECT:Drv", "#%d" % u], fields="name")
    assert g["results"][0]["uid"] == u and g["results"][0]["value"]["name"] == "DrvRenamed", g["results"]
    assert g["errors"][0]["ref"] == "OBJECT:Drv" and "objects" in g["errors"][0].get("tried", []), g["errors"]
    # R16: NO uid index or cache (full bpy.data scan per call); a stale hit is impossible by construction
    assert bpy.app.driver_namespace.get("blendermcp_driver_uids") is None, "R16: no uid cache, but blendermcp_driver_uids exists"
    # the same uid after a bpy-side rename and after a mesh of the same name appears
    bpy.data.objects["DrvRenamed"].name = "DrvTwice"
    bpy.data.meshes["DrvMesh"].name = "DrvTwice"
    try:
        g2 = dok("drv_get", refs=["#%d" % u, "OBJECT:DrvTwice", "MESH:DrvTwice"], fields="name")
        assert [x["id_type"] for x in g2["results"]] == ["OBJECT", "OBJECT", "MESH"] and g2["results"][0]["uid"] == u, A(g2["results"])
    finally:
        bpy.data.objects["DrvTwice"].name = "DrvRenamed"
        bpy.data.meshes["DrvTwice"].name = "DrvMesh"
    return "rename via #uid: old name gone from find/OBJECT:, #uid resolves; no uid cache (R16); IDTYPE disambiguates same-named object/mesh"


step("DRV-4 rename by uid + uid cache verify-on-hit", d4, tag="DRV")


def d4c():
    r = dok("drv_context", must=["mode", "active_object", "selected", "scene", "view_layer", "window", "areas", "frame_current", "background"])
    assert r["mode"] == "OBJECT" and r["background"] is True and r["window"] is True, (r["mode"], r["background"], r["window"])
    assert isinstance(r["areas"], list) and r["areas"] and all("type" in a and isinstance(a["regions"], list) for a in r["areas"]), r["areas"][:2]
    # 12.2.1: every ID mentioned in a reply is {uid, id_type, name}, the scene included
    assert r["scene"] == {"uid": bpy.context.scene.session_uid, "id_type": "SCENE", "name": bpy.context.scene.name}, r["scene"]
    assert r["view_layer"] == bpy.context.view_layer.name and r["frame_current"] == bpy.context.scene.frame_current
    assert r["active_object"] is None or isinstance(r["active_object"].get("uid"), int), r["active_object"]
    assert isinstance(r["selected"], list) and r["edit_object"] is None and r["pose_object"] is None
    ob = bpy.data.objects["DrvRenamed"]
    prev_active = bpy.context.view_layer.objects.active
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    try:
        r2 = dok("drv_context", fields="active_object,selected")
        assert r2["active_object"]["uid"] == U["ob"] and [s["uid"] for s in r2["selected"]] == [U["ob"]], r2
    finally:
        ob.select_set(False)
        bpy.context.view_layer.objects.active = prev_active
    _enc_ok(r)
    return "headless: window True (R20), background True, %d areas, active/selected as uids" % len(r["areas"])


step("DRV-4c context_info", d4c, readonly=True, tag="DRV")


# ---- T2 (after Linus L2/L3): DRV-2b batch + $, DRV-4b snapshot/diff, DRV-5 call_operator, DRV-6 call_method,
# DRV-7 transaction (R17 real revert, R22 keys), DRV-8 batch limits + rollback (R27 wall cap), DRV-9 reply cap.


def _changed_for(d, uid):
    return d["changed"].get(str(uid)) or d["changed"].get(uid) or {}


def d2b():
    r = dok("drv_batch", must=["results", "rolled_back", "undo_available", "stopped_at", "elapsed_ms", "chars", "truncated"],
            commands=[{"id": "mesh", "type": "drv_new", "params": {"collection": "meshes", "args": ["DrvBMesh"]}},
                      {"id": "ob", "type": "drv_new", "params": {"collection": "objects", "args": ["DrvB", "$mesh.uid"], "link_to": "scene"}},
                      {"id": "get", "type": "drv_get", "params": {"refs": ["OBJECT:DrvB"], "fields": "name"}}], undo="NONE")
    assert [x["status"] for x in r["results"]] == ["ok", "ok", "ok"] and [x["id"] for x in r["results"]] == ["mesh", "ob", "get"], A(r["results"])
    mu, ou = r["results"][0]["result"]["uid"], r["results"][1]["result"]["uid"]
    ob = bpy.data.objects["DrvB"]
    assert mu != ou and ob.session_uid == ou and ob.data.session_uid == mu and ob.name in bpy.context.scene.collection.objects
    assert r["results"][2]["result"]["results"][0]["uid"] == ou, "$mesh.uid did not resolve to the mesh created in the same batch"
    assert r["rolled_back"] is False and r["stopped_at"] is None and all(isinstance(x["elapsed_ms"], (int, float)) for x in r["results"])
    f = dok("drv_find", name="DrvB")
    assert f["count"] == 1 and f["items"][0]["uid"] == ou
    # a nested json path over a list resolves
    dok("drv_batch", commands=[{"id": "get", "type": "drv_get", "params": {"refs": ["OBJECT:DrvB"], "fields": "name"}},
                               {"id": "set", "type": "drv_set", "params": {"sets": [['OBJECT:DrvB/["tag"]', "$get.results[0].value.name"]]}}], undo="NONE")
    assert bpy.data.objects["DrvB"]["tag"] == "DrvB", "nested $get.results[0].value.name not substituted"
    # a missing id is that command's error and the batch stops there (stop_on_error default)
    r3 = dcall("drv_batch", commands=[{"type": "get_version"}, {"id": "x", "type": "drv_set", "params": {"sets": [["OBJECT:DrvB/location", "$nope.uid"]]}},
                                      {"type": "get_version"}], undo="NONE")
    assert r3["results"][1]["status"] == "error" and "nope" in r3["results"][1]["error"] and r3["results"][2]["status"] == "skipped" and r3["stopped_at"] == 1, A(r3)
    # refs=False: the literal "$ob.uid" reaches drv_get and lands in its errors
    r4 = dcall("drv_batch", commands=[{"type": "drv_get", "params": {"refs": ["$ob.uid"]}}], refs=False, undo="NONE")
    inner = r4["results"][0].get("result") or {}
    assert r4["results"][0]["status"] == "error" or (inner.get("errors") and inner["errors"][0]["ref"] == "$ob.uid"), A(r4)
    # nested batch refused; unknown handler named; a gated legacy command inside a batch gets the gate text (12.5)
    r5 = dcall("drv_batch", commands=[{"type": "drv_batch", "params": {"commands": []}}, {"type": "no_such_handler"},
                                      {"type": "get_polyhaven_categories"}], stop_on_error=False, undo="NONE")
    st = [x["status"] for x in r5["results"]]
    assert st == ["error", "error", "error"] and r5["stopped_at"] is None, A(r5)
    assert "nested" in r5["results"][0]["error"].lower() and "no_such_handler" in r5["results"][1]["error"] and "disabled" in r5["results"][2]["error"].lower(), A(r5["results"])
    bpy.data.objects.remove(bpy.data.objects["DrvB"], do_unlink=True)
    bpy.data.meshes.remove(bpy.data.meshes["DrvBMesh"])
    return "mesh+object in one batch via $mesh.uid (uids %d/%d), nested $path, missing id stops, refs=False literal, nested/unknown/gated errors" % (mu, ou)


step("DRV-2b batch + $ substitution", d2b, tag="DRV")


def d4b():
    u = U["ob"]
    s = dok("drv_snapshot", must=["name", "captured_at", "scope", "count", "ids", "tree", "chars", "truncated"], name="a")
    assert s["scope"] == "SCENE" and s["count"] >= 1, (s["scope"], s["count"])
    ent = s["ids"].get(str(u)) or s["ids"].get(u)
    assert ent and ent["id_type"] == "OBJECT" and ent["name"] == "DrvRenamed", A(ent)
    for k in ("type", "location", "rotation_euler", "scale", "hide_viewport", "hide_render", "users"):
        assert k in ent, "snapshot entry lacks default field %s" % k
    assert isinstance(s["tree"], list) and any(t["uid"] == u for t in s["tree"]) and all("children" in t for t in s["tree"]), A(s["tree"][:2])
    s0 = dok("drv_snapshot", name="b0", return_tree=False)
    assert set(s0) == {"success", "name", "count", "chars"}, A(s0)
    dok("drv_set", sets=[["#%d/name" % u, "DrvTwice"]])
    d = dok("drv_diff", must=["a", "b", "added", "removed", "renamed", "changed", "chars", "truncated"], a="a")
    assert d["renamed"] == [{"uid": u, "before": "DrvRenamed", "after": "DrvTwice"}] and d["added"] == [] and d["removed"] == [], A(d)
    assert "name" not in _changed_for(d, u), "I6: a rename appears only in renamed: %s" % A(d["changed"])
    ob = bpy.data.objects["DrvTwice"]
    x0 = ob.location.x
    ob.location.x = x0 + 2.5
    bpy.ops.mesh.primitive_cube_add(location=(5, 5, 5))
    cube = bpy.context.active_object
    cube.name = "DrvDiffCube"
    d2 = dok("drv_diff", a="a")
    ch = _changed_for(d2, u)
    assert "location" in ch and abs(ch["location"][0][0] - x0) < 1e-5 and abs(ch["location"][1][0] - (x0 + 2.5)) < 1e-5, A(ch)
    assert [x["name"] for x in d2["added"]] == ["DrvDiffCube"] and d2["added"][0]["uid"] == cube.session_uid and d2["added"][0]["id_type"] == "OBJECT", A(d2["added"])
    bpy.data.objects.remove(cube, do_unlink=True)
    d3 = dok("drv_diff", a="a", b="b0")
    assert d3["added"] == [] and d3["removed"] == [] and d3["renamed"] == [] and not _changed_for(d3, u), A(d3)
    e = derr("drv_diff", "nope", a="nope")
    assert "a" in e.get("valid", []) and "b0" in e.get("valid", []), A(e)
    reg = ok(call("get_version"))["registries"]
    assert reg.get("blendermcp_driver_snapshots", 0) >= 2, A(reg)
    sc = dok("drv_snapshot", name="cap", max_chars=400)
    assert sc["chars"] <= 400 and sc["truncated"], (sc["chars"], sc["truncated"][:2])
    dc = dok("drv_diff", a="a", max_chars=200)
    assert dc["chars"] <= 200, dc["chars"]
    dok("drv_set", sets=[["#%d/name" % u, "DrvRenamed"]])
    bpy.data.objects["DrvRenamed"].location.x = x0
    return "snapshot a (count %d), rename -> renamed only, move+add -> changed.location + added, diff a vs b0 clean, cap honoured" % s["count"]


step("DRV-4b scene_snapshot + scene_diff", d4b, tag="DRV")


def d5():
    u = U["ob"]
    ob = bpy.data.objects["DrvRenamed"]
    ctx = {"active": "#%d" % u, "selected": ["#%d" % u]}
    r = dok("drv_op", must=["result", "reports", "context_applied", "elapsed_ms", "modal"], op="object.modifier_add", params={"type": "BEVEL"}, context=ctx)
    assert r["result"] == ["FINISHED"] and r["modal"] is False and isinstance(r["reports"], list), A(r)
    assert r["context_applied"]["active"] == u and r["context_applied"]["selected"] == [u], A(r["context_applied"])
    assert ob.modifiers[0].type == "BEVEL"
    g = dok("drv_get", refs=["#%d" % u], fields="modifiers[]", depth=1)
    assert g["results"][0]["value"]["modifiers"]["items"][0]["type"] == "BEVEL", A(g["results"][0]["value"])
    npoly = len(ob.data.polygons)
    e = derr("drv_op", op="mesh.bevel", params={"offset": 0.1}, context=ctx)
    assert "poll_message" in e and "context_applied" in e and len(ob.data.polygons) == npoly and bpy.context.mode == "OBJECT", A(e)
    assert "context is incorrect" in e.get("rna_error", "") and e["context_applied"].get("mode") is None, A(e)   # R21 + oracle
    prev_active = bpy.context.view_layer.objects.active
    prev_sel = sorted(o.name for o in bpy.context.view_layer.objects if o.select_get())
    r2 = dok("drv_op", op="mesh.bevel", params={"offset": 0.1, "segments": 2}, context=dict(ctx, mode="EDIT"))
    assert r2["result"] == ["FINISHED"] and r2["context_applied"]["mode"] == "EDIT" and bpy.context.mode == "OBJECT", A(r2)
    assert len(ob.data.polygons) > npoly, "bevel did not run"
    assert bpy.context.view_layer.objects.active == prev_active and sorted(o.name for o in bpy.context.view_layer.objects if o.select_get()) == prev_sel
    assert dok("drv_context", fields="mode")["mode"] == "OBJECT"
    r3 = dok("drv_op", op="mesh.select_all", params={"action": "SELECT"}, context=dict(ctx, mode="EDIT", keep=True))
    assert r3["result"] == ["FINISHED"] and bpy.context.mode == "EDIT_MESH", "keep=True must leave edit mode on"
    bpy.ops.object.mode_set(mode="OBJECT")
    e1 = derr("drv_op", "bogus", op="object.select_all", params={"bogus": 1})
    assert "closest" in e1 and 'keyword "bogus" unrecognized' in e1.get("rna_error", ""), A(e1)
    # R37: params are validated BEFORE poll, so a misspelt key is reported even in OBJECT mode (where mesh.bevel cannot
    # poll) and nothing runs
    npoly2 = len(ob.data.polygons)
    e1b = derr("drv_op", "offest", op="mesh.bevel", params={"offest": 0.1}, context=ctx)
    assert "offset" in e1b.get("closest", []) and len(ob.data.polygons) == npoly2 and bpy.context.mode == "OBJECT", A(e1b)
    e2 = derr("drv_op", "NOPE", op="object.select_all", params={"action": "NOPE"})
    assert "DESELECT" in e2.get("valid", []), A(e2)
    e3 = derr("drv_op", "mesh.", op="mesh.no_such", context=ctx)
    assert e3.get("closest"), A(e3)
    # R19: an unknown `extra` key is refused against the 16-key whitelist and the operator is NOT called
    nmods = len(ob.modifiers)
    e4 = derr("drv_op", "edit_objekt", op="object.modifier_add", params={"type": "SUBSURF"}, context=dict(ctx, extra={"edit_objekt": "#%d" % u}))
    assert "edit_object" in e4.get("valid", []) and len(e4["valid"]) == 16 and len(ob.modifiers) == nmods, A(e4)
    for op in ("wm.quit_blender", "wm.read_factory_settings", "screen.animation_play", "script.reload", "wm.console_toggle",
               "wm.window_new", "wm.window_fullscreen_toggle"):
        e = derr("drv_op", "refused", op=op)
        assert e.get("refused") is True, A(e)
    e = derr("drv_op", "refused", op="preferences.addon_disable", params={"module": addon.__name__})
    assert e.get("refused") is True, A(e)
    ok(call("get_version"))
    t0 = time.time()
    e5 = derr("drv_op", "EXEC_DEFAULT", op="transform.translate", params={"value": [1, 0, 0]}, exec_context="INVOKE_DEFAULT", context=ctx)
    assert time.time() - t0 < 0.05, "INVOKE refusal must happen before the call (oracle 5b: < 50 ms)"
    e6 = derr("drv_op", "GUI", op="view3d.view_all", context={"area": "VIEW_3D", "region": "WINDOW"})
    assert e6.get("background") is True, "R20: the GUI-only error carries background:true: %s" % A(e6)
    ob.modifiers.remove(ob.modifiers[0])
    return "modifier_add FINISHED w/ context echo; bevel poll error w/o EDIT, FINISHED with EDIT + mode/selection restored, keep=True; closest/valid/whitelist; 8 refused ops; INVOKE + viewport refused headless"


step("DRV-5 call_operator context/poll/refused", d5, tag="DRV")


def d6():
    u = U["ob"]
    r = dok("drv_call", must=["value", "chars", "truncated"], ref="#%d" % u, method="evaluated_get", depsgraph=True)
    assert isinstance(r["value"], dict) and isinstance(r["value"].get("uid"), int) and r["value"]["id_type"] == "OBJECT", A(r)
    dok("drv_new", collection="meshes", args=["DrvPy"])
    dok("drv_call", ref="MESH:DrvPy", method="from_pydata", args=[[[0, 0, 0], [1, 0, 0], [0, 1, 0]], [], [[0, 1, 2]]])
    g = dok("drv_get", refs=["MESH:DrvPy"], fields="polygons,vertices")
    assert g["results"][0]["value"] == {"polygons": {"count": 1}, "vertices": {"count": 3}}, A(g["results"][0]["value"])
    r3 = dok("drv_call", ref="MESH:DrvPy", method="validate")
    assert r3["value"] is False and abs(bpy.data.meshes["DrvPy"].polygons[0].area - 0.5) < 1e-6
    r4 = dcall("drv_call", ref="MESH:DrvPy", method="calc_tangents")
    assert "Traceback" not in json.dumps(r4), A(r4)
    r5 = dok("drv_call", ref="#%d" % u, method="to_mesh")
    assert isinstance(r5["value"], dict) and (r5["value"].get("struct") == "Mesh" or r5["value"].get("id_type") == "MESH"), A(r5)
    dok("drv_call", ref="#%d" % u, method="to_mesh_clear")
    n_before = len(bpy.data.meshes)
    r6 = dok("drv_call", ref="data/meshes", method="new_from_object", args=["#%d" % u])
    assert r6["value"]["id_type"] == "MESH" and len(bpy.data.meshes) == n_before + 1, A(r6)
    e1 = derr("drv_call", "evaluated_get", ref="#%d" % u, method="evaluated_gett")
    assert "evaluated_get" in e1.get("closest", []), A(e1)
    rp = dok("drv_call", ref="SCENE:Scene/render", method="path_from_id", args=["resolution_x"])
    assert rp["value"] == "render.resolution_x", A(rp)
    e2 = derr("drv_call", "depsgraph", ref="#%d" % u, method="evaluated_get")
    derr("drv_call", ref="#%d" % u, method="from_pydata")
    en = derr("drv_call", "NoSuch", ref="OBJECT:NoSuch", method="evaluated_get", depsgraph=True)
    assert "objects" in en.get("tried", []), A(en)
    bpy.data.meshes.remove(bpy.data.meshes[r6["value"]["name"]])
    bpy.data.meshes.remove(bpy.data.meshes["DrvPy"])
    _enc_ok(r)
    return "evaluated_get -> uid; from_pydata 3 verts 1 poly, validate False, calc_tangents no traceback; to_mesh/clear; data/meshes.new_from_object; closest + missing depsgraph"


step("DRV-6 call_method", d6, tag="DRV")


def d7():
    u = U["ob"]
    ob = bpy.data.objects["DrvRenamed"]
    before = tuple(ob.location)
    # D1.2/R22: an object that exists BEFORE the transaction and an UNPUSHED edit made before BEGIN both survive ROLLBACK
    # (the "mcp:<name>:pre" push stops the undo walk from entering the user's earlier work)
    pre = bpy.data.objects.new("PreTxn", None)
    bpy.context.scene.collection.objects.link(pre)
    ob.scale.x = 3.0
    r = dok("drv_transaction", must=["action", "name", "undo_calls", "marker_present", "undo_available", "open"], action="BEGIN", name="t1")
    assert r["open"] is True and r["name"] == "t1" and r["undo_available"] is True and r["marker_present"] is True and r["undo_calls"] == 0, A(r)
    assert isinstance(bpy.context.scene.get("_mcp_txn"), int), "R22: Scene idprop _mcp_txn holds the seq while open"
    st = dok("drv_transaction", action="STATUS")
    assert st["open"] is True and st["name"] == "t1", A(st)
    dok("drv_set", sets=[["#%d/location" % u, [5, 5, 5]]])
    dok("drv_op", op="object.modifier_add", params={"type": "SUBSURF"}, context={"active": "#%d" % u, "selected": ["#%d" % u]})
    assert tuple(bpy.data.objects["DrvRenamed"].location) == (5.0, 5.0, 5.0)
    rb = dok("drv_transaction", action="ROLLBACK")
    ob = bpy.data.objects["DrvRenamed"]
    assert rb["undo_calls"] >= 1 and rb["marker_present"] is False and rb["open"] is False and rb["undo_available"] is True, A(rb)
    assert tuple(ob.location) == before, "R17: ROLLBACK must restore the location, got %s" % (tuple(ob.location),)
    assert not any(m.type == "SUBSURF" for m in ob.modifiers), "the operator inside the transaction was not undone"
    assert ob.session_uid == u and "_mcp_txn" not in bpy.context.scene, "R22: _mcp_txn must be gone after ROLLBACK"
    assert bpy.data.objects.get("PreTxn") is not None and ob.scale.x == 3.0, "ROLLBACK walked past BEGIN into pre-transaction edits"
    assert dok("drv_transaction", action="STATUS")["open"] is False
    dok("drv_transaction", action="BEGIN", name="t2")
    e = derr("drv_transaction", "t2", action="BEGIN", name="t3")
    assert e.get("open") is True, A(e)
    c = dok("drv_transaction", action="COMMIT")
    assert c["open"] is False and "_mcp_txn" not in bpy.context.scene, "R22: _mcp_txn must be gone after COMMIT"
    e3 = derr("drv_transaction", "no transaction", action="ROLLBACK")
    e2 = derr("drv_transaction", action="NOPE")
    assert "BEGIN" in json.dumps(e2), A(e2)
    ob.scale.x = 1.0
    bpy.data.objects.remove(bpy.data.objects["PreTxn"], do_unlink=True)
    return "BEGIN/STATUS, set+op inside, ROLLBACK -> location %s restored, modifier gone, pre-BEGIN object + unpushed scale kept, undo_calls %d, marker gone; nested refused; COMMIT clears" % (before, rb["undo_calls"])


step("DRV-7 transaction BEGIN/ROLLBACK/COMMIT (R17 real revert)", d7, tag="DRV")



def d8():
    u = U["ob"]
    R = "#%d" % u
    pre = bpy.data.objects.new("PreBatch", None)
    bpy.context.scene.collection.objects.link(pre)
    before = list(bpy.data.objects["DrvRenamed"].location)
    # oracle step 8: the 3rd command fails through R30 (an rna_set whose items all fail -> success:false)
    cmds = [{"type": "drv_set", "params": {"sets": [[R + "/location", [1, 1, 1]]]}},
            {"type": "drv_set", "params": {"sets": [[R + "/location", [2, 2, 2]]]}},
            {"type": "drv_set", "params": {"sets": [[R + "/nope", 1]]}},
            {"type": "drv_set", "params": {"sets": [[R + "/location", [3, 3, 3]]]}},
            {"type": "drv_get", "params": {"refs": [R]}}]
    r = dcall("drv_batch", commands=cmds)
    assert [x["status"] for x in r["results"]] == ["ok", "ok", "error", "skipped", "skipped"] and r["stopped_at"] == 2, A(r)
    assert r["rolled_back"] is True and r["undo_available"] is True and r["success"] is False, A({k: r[k] for k in ("rolled_back", "undo_available", "success")})
    assert list(bpy.data.objects["DrvRenamed"].location) == before, "R17: undo=AUTO must revert commands 1 and 2"
    assert bpy.data.objects.get("PreBatch") is not None and "_mcp_batch" not in bpy.context.scene, "rollback walked past the batch marker"
    r3 = dcall("drv_batch", commands=cmds, stop_on_error=False, undo="NONE")
    assert [x["status"] for x in r3["results"]] == ["ok", "ok", "error", "ok", "ok"] and r3["stopped_at"] is None and r3["rolled_back"] is False, A(r3)
    assert list(bpy.data.objects["DrvRenamed"].location) == [3.0, 3.0, 3.0]
    e = derr("drv_batch", "201", commands=[cmds[0]] * 201)
    assert list(bpy.data.objects["DrvRenamed"].location) == [3.0, 3.0, 3.0], "a refused batch ran its first command"
    r200 = dcall("drv_batch", commands=[{"type": "get_version"}] * 200, undo="NONE")
    assert len(r200["results"]) == 200 and all(x["status"] == "ok" for x in r200["results"]), "exactly 200 must run"
    rn = dcall("drv_batch", commands=[{"type": "drv_batch", "params": {"commands": []}}], undo="NONE")
    assert rn["results"][0]["status"] == "error" and "nested" in rn["results"][0]["error"].lower(), A(rn)
    rg = dcall("drv_batch", commands=[{"type": "get_polyhaven_categories", "params": {"asset_type": "hdris"}}, cmds[0]], stop_on_error=False, undo="NONE")
    assert rg["results"][0]["status"] == "error" and "disabled" in rg["results"][0]["error"].lower() and rg["results"][1]["status"] == "ok", A(rg)
    rz = dcall("drv_batch", commands=[{"id": "a", "type": "drv_get", "params": {"refs": [R]}},
                                      {"type": "drv_set", "params": {"sets": [[R + "/location", "$zzz.value"]]}}], undo="NONE")
    assert rz["results"][1]["status"] == "error" and "zzz" in rz["results"][1]["error"], A(rz)
    assert addon._DRV_BATCH_MAX == 200 and addon._DRV_BATCH_WALL_S == 60.0 and addon._DRV_REPLY_MAX_DEFAULT == 24000
    # 8b (R27): the wall cap is checked BETWEEN commands; at 0.0 only the first command runs
    saved = addon._DRV_BATCH_WALL_S
    addon._DRV_BATCH_WALL_S = 0.0
    try:
        rw = dcall("drv_batch", commands=cmds[:3], undo="NONE")
    finally:
        addon._DRV_BATCH_WALL_S = saved
    assert rw["stopped_at"] == 1 and [x["status"] for x in rw["results"]] == ["ok", "skipped", "skipped"], A(rw)
    # 12.2.2 batch row: "> wall -> stop, remaining skipped, ROLLBACK when undo=AUTO and stop_on_error"; the reply must
    # also say why the rest was skipped (rule 5, errors teach). F12: at bb5c6ef3 success stays true and nothing rolls back.
    dok("drv_set", sets=[[R + "/location", before]])
    addon._DRV_BATCH_WALL_S = 0.0
    try:
        rw2 = dcall("drv_batch", commands=cmds[:3])          # undo=AUTO, stop_on_error=True
    finally:
        addon._DRV_BATCH_WALL_S = saved
    assert [x["status"] for x in rw2["results"]] == ["ok", "skipped", "skipped"] and rw2["stopped_at"] == 1, A(rw2)
    assert rw2["rolled_back"] is True and list(bpy.data.objects["DrvRenamed"].location) == before, \
        "wall-capped AUTO batch must roll back the completed commands: %s" % A({k: rw2[k] for k in ("success", "rolled_back", "stopped_at")})
    assert rw2["success"] is False and "wall" in json.dumps(rw2).lower(), "a wall-capped batch is not a success and must name the wall cap: %s" % A(rw2)
    dok("drv_set", sets=[[R + "/location", before]])
    bpy.data.objects.remove(bpy.data.objects["PreBatch"], do_unlink=True)
    return "5-cmd batch: R30 error at 2, 2 skipped, rolled_back True + location restored, pre-batch object kept; stop_on_error=False runs all; 201 refused w/o running; 200 runs; nested/gated/$zzz errors; wall cap 0.0 -> stopped_at 1"


step("DRV-8 batch stop/rollback/201/wall cap", d8, tag="DRV")




# DRV-10 (events, L4 only) is added as T4 when the Lead pulls L4 in. The object DrvRenamed (U["ob"]) survives until DRV-11.


# ---- G11 (b): execute_code result_expr + structured error, old shape unchanged
def d12b():
    old = ok(call("execute_code", code="print('drv-hi')"))
    assert old.get("executed") is True and "drv-hi" in str(old.get("result")), old
    r = ok(call("execute_code", code="x = 40", result_expr="x + 2"))
    assert r.get("value") == 42 and r.get("executed") is True, r
    r2 = ok(call("execute_code", code="ob = bpy.data.objects['DrvRenamed']", result_expr="ob"))
    assert r2["value"] == {"uid": U["ob"], "id_type": "OBJECT", "name": "DrvRenamed"}, r2["value"]
    e = dcall("execute_code", code="y = 1\nraise ValueError('boom')")
    assert "ValueError: boom" in str(e.get("error")), e
    assert isinstance(e.get("traceback"), list) and 1 <= len(e["traceback"]) <= 3 and e.get("line") == 2, e
    return "stdout shape kept; result_expr -> value 42 / ID as {uid,id_type,name}; exception -> error+traceback(<=3)+line 2"


step("DRV-12b execute_code result_expr + error dict", d12b, tag="DRV")


# ---- R-B2-43 (F-B2-4): a dict result_expr round-trips as a JSON object, never as its key list
def d12c():
    r = ok(call("execute_code", code="d = {'a': 1, 'b': [1, 2], 'c': {'x': 2.5}}", result_expr="d"))
    assert r.get("value") == {"a": 1, "b": [1, 2], "c": {"x": 2.5}}, "dict result_expr: %s" % A(json.dumps(r.get("value"))[:200])
    r = ok(call("execute_code", code="l = [{'n': 1}, {'n': 2, 'loc': [0, 1]}]", result_expr="l"))
    assert r.get("value") == [{"n": 1}, {"n": 2, "loc": [0, 1]}], "list of dicts: %s" % A(json.dumps(r.get("value"))[:200])
    r = ok(call("execute_code", code="t = ({'k': 'v'}, 7)", result_expr="t"))
    assert r.get("value") == [{"k": "v"}, 7], "dict in a tuple: %s" % A(json.dumps(r.get("value"))[:200])
    r = ok(call("execute_code", code="m = {1: 'one', (2, 3): 'pair'}", result_expr="m"))
    assert r.get("value") == {"1": "one", "(2, 3)": "pair"}, "non-str keys: %s" % A(json.dumps(r.get("value"))[:200])
    return "dict, list of dicts, dict in a tuple and non-str keys round-trip through execute_code result_expr (R-B2-43)"


step("DRV-12c execute_code result_expr dict round-trips (R-B2-43)", d12c, readonly=True, tag="DRV")


# ---- G11 (c): get_version.registries over every blendermcp_* driver_namespace registry
def d12d():
    r = ok(call("get_version"), must=["addon", "protocol", "blender", "python", "addon_file", "background", "registries"])
    assert r["protocol"] == 1 and r["addon"] == ".".join(str(x) for x in addon.bl_info["version"]), (r["protocol"], r["addon"])
    reg = r["registries"]
    assert isinstance(reg, dict) and all(isinstance(v, int) for v in reg.values()), reg
    ok(call("settings_snapshot", name="drvreg"))
    try:
        reg2 = ok(call("get_version"))["registries"]
        assert reg2.get("blendermcp_settings_snapshots", 0) >= 1, reg2
        assert reg2["blendermcp_settings_snapshots"] == reg.get("blendermcp_settings_snapshots", 0) + 1, (reg, reg2)
    finally:
        ok(call("delete_settings_snapshot", name="drvreg"))
    assert "blendermcp_driver_uids" not in reg2, "R16: no uid cache registry"
    assert all(k.startswith("blendermcp_") for k in reg2), "O3 ruling: only blendermcp_* registries, those that exist: %s" % A(list(reg2))
    return "registries %s -> snapshot adds one to blendermcp_settings_snapshots" % A(json.dumps(reg))


step("DRV-12d get_version.registries", d12d, readonly=True, tag="DRV")


# ---- doc step 11: data_remove with orphan purge; the uid is gone for good
def d11():
    u, m = U["ob"], U["mesh"]
    r = dok("drv_remove", must=["results", "purged"], refs=["#%d" % u], purge_orphans=True)
    x = r["results"][0]
    assert x["ref"] == "#%d" % u and x["removed"] is True and x["users_before"] == 1, x
    assert bpy.data.objects.get("DrvRenamed") is None and bpy.data.meshes.get("DrvMesh") is None, "object or orphan mesh survived"
    # orphans_purge is recursive over bpy.data: the unused DrvMat material (0 users) goes with the mesh
    assert bpy.data.materials.get("DrvMat") is None, "orphan material survived purge_orphans=True"
    assert isinstance(r["purged"], int) and r["purged"] >= 2, r["purged"]
    g = dok("drv_get", refs=["#%d" % u, "#%d" % m], fields="name")
    assert g["results"] == [] and len(g["errors"]) == 2 and all("tried" in e for e in g["errors"]), g
    # a mesh with users and do_unlink=False is refused per item
    keep = bpy.data.meshes.new("DrvKeep")
    ko = bpy.data.objects.new("DrvKeepOb", keep)
    bpy.context.scene.collection.objects.link(ko)
    r2 = dcall("drv_remove", refs=["MESH:DrvKeep"], do_unlink=False)
    y = r2["results"][0]
    assert r2["success"] is False and y["removed"] is False and y["users_before"] >= 1 and "do_unlink" in y.get("error", ""), r2
    assert bpy.data.meshes.get("DrvKeep") is not None, "mesh with users removed despite do_unlink=False"
    ko_uid = ko.session_uid   # read before the remove: the Python wrapper dies with the ID
    r3 = dcall("drv_remove", refs=["#%d" % ko_uid, "MESH:DrvKeep", "OBJECT:DrvParent", "MATERIAL:DrvMat"])
    st = [(z["ref"], z["removed"]) for z in r3["results"]]
    assert r3["success"] is False and st[:3] == [("#%d" % ko_uid, True), ("MESH:DrvKeep", True), ("OBJECT:DrvParent", True)], A(st)
    assert st[3] == ("MATERIAL:DrvMat", False) and "materials" in r3["results"][3].get("tried", []), "already-purged material must report not found with tried: %s" % A(r3["results"][3])
    assert bpy.data.meshes.get("DrvKeep") is None and bpy.data.objects.get("DrvParent") is None
    scenes = sorted(s.name for s in bpy.data.scenes)
    rs = dcall("drv_remove", refs=["SCENE:%s" % bpy.context.scene.name])
    assert sorted(s.name for s in bpy.data.scenes) == scenes, "the active scene was removed"
    assert "error" in rs or rs["results"][0]["removed"] is False, rs
    return "object+orphan mesh removed (purged %d), #uids -> not found with tried; mesh with users refused w/o unlink; active scene kept" % r["purged"]


step("DRV-11 data_remove + purge + refused with users", d11, tag="DRV")


# ---- doc step 9 runs LAST: a deep SCENE read crashed Blender 4.3.2 (EXCEPTION_ACCESS_VIOLATION, F9); keeping it last
# preserves every other step's evidence on 4.3.2 until the add-on guards that path.
def d9():
    # F9 coverage (Lead m_52b1c3b7): the crash path is a depth=2 fields="all" walk under the scene (tool_settings ->
    # Paint.eraser_brush dereference on 4.3.2); R33 says the walker never dereferences pointers on ToolSettings /
    # Paint-derived structs and reports that once in `truncated`. Both roots are read, uncapped first.
    rc = dok("drv_get", refs=["context/scene"], fields="all", depth=2, max_chars=200000)
    assert rc["results"] and rc["results"][0]["id_type"] == "SCENE" and "tool_settings" in rc["results"][0]["value"], A(list(rc["results"][0]["value"])[:20])
    assert any("tool_settings" in t or "Paint" in t or "pointer" in t.lower() for t in rc["truncated"]), "R33: skipped pointer walk must be reported once: %s" % A(rc["truncated"][:5])
    r = dok("drv_get", refs=["SCENE:Scene"], fields="all", depth=2, max_chars=2000)
    assert r["chars"] <= 2000 and r["truncated"] and len(json.dumps(r)) <= 2400, (r["chars"], len(json.dumps(r)), r["truncated"][:3])
    assert all(isinstance(t, str) for t in r["truncated"]), r["truncated"][:3]
    # default cap (no max_chars) at depth=2; depth=3 is a separate last step (DRV-9b) once the second 4.3.2 guard lands
    r2 = dok("drv_get", refs=["SCENE:Scene"], fields="all", depth=2)
    assert r2["chars"] <= 24000 and len(json.dumps(r2)) <= 24400, (r2["chars"], len(json.dumps(r2)))
    r3 = dok("drv_get", refs=["SCENE:Scene"], fields="all", depth=2, max_chars=50)
    assert "results" in r3 and r3["truncated"] and r3["chars"] <= 50 + 400, A(r3)
    d = dok("drv_describe", what="Object", max_chars=800)
    assert d["chars"] <= 800 and d["truncated"], (d["chars"], d["truncated"][:2])
    f = dok("drv_find", id_type="MESH", scope="DATA", fields="name,vertices", max_chars=300)
    assert f["chars"] <= 300, f["chars"]
    return "SCENE depth=2 cap 2000 -> %d chars, %d truncated entries; default cap 24000 holds at depth=3; describe/find capped" % (r["chars"], len(r["truncated"]))


step("DRV-9 reply cap", d9, readonly=True, tag="DRV")


# ---- DRV-9b (Lead m_264c8df8): the second 4.3.2 crash site was a depth-3 walk under the scene (CurveMapping.tone);
# with the R33 struct guard confirmed this runs LAST: a depth=3 fields="all" read of SCENE:Scene at a small cap.
# The assertion that matters on 4.3.2 is that the process survives to SUMMARY and exits 0.
def d9b():
    r = dok("drv_get", refs=["SCENE:Scene"], fields="all", depth=3, max_chars=4000)
    assert r["results"] and r["results"][0]["id_type"] == "SCENE", A(r["results"][:1])
    assert r["chars"] <= 4000 and len(json.dumps(r)) <= 4400 and r["truncated"], (r["chars"], len(json.dumps(r)), r["truncated"][:2])
    assert any("tool_settings" in t or "Paint" in t or "CurveMapping" in t or "pointer" in t.lower() for t in map(str, r["truncated"])), \
        "R33: skipped ToolSettings/Paint/CurveMapping walk must be reported: %s" % A(r["truncated"][:4])
    return "SCENE depth=3 cap 4000 -> %d chars, %d truncated entries, no crash" % (r["chars"], len(r["truncated"]))


step("DRV-9b deep read depth=3 (R33 guard, 4.3.2 crash site)", d9b, readonly=True, tag="DRV")


# ---- B2-G3.5 add-on review RA-9 / RA-12 (docs/REVIEW-B2-addon.md). LAST in the driver block on purpose: RA-9 deletes a
# transaction marker, and anything that follows must not inherit that undo history.
def d_ra9():
    dok("drv_transaction", action="BEGIN", name="t9")
    assert isinstance(bpy.context.scene.get("_mcp_txn"), int)
    del bpy.context.scene["_mcp_txn"]                                   # the marker vanishes (e.g. a user undo past it)
    e = derr("drv_transaction", "marker", action="ROLLBACK")
    assert e.get("success") is False and e.get("undo_calls", 0) == 0, A(e)
    st = dcall("drv_transaction", action="STATUS")
    if st.get("open"):
        dok("drv_transaction", action="COMMIT")
    assert "_mcp_txn" not in bpy.context.scene
    return "ROLLBACK with the marker gone -> error, nothing reverted, undo_calls 0 (RA-9)"


step("DRV-RA9 ROLLBACK without a marker is an error (RA-9)", d_ra9, tag="DRV")


def d_ra12():
    dok("drv_new", collection="meshes", args=["RaMesh"])
    r = dok("drv_call", ref="data/objects", method="new", args=["RaOb", "MESH:RaMesh"])          # pointer arg by IDTYPE:Name
    ob = bpy.data.objects.get("RaOb")
    assert ob is not None and ob.data is bpy.data.meshes["RaMesh"], "drv_call did not resolve the pointer arg like drv_new (RA-12)"
    uid = bpy.data.meshes["RaMesh"].session_uid
    dok("drv_call", ref="data/objects", method="new", args=["RaOb2", "#%d" % uid])
    assert bpy.data.objects["RaOb2"].data is bpy.data.meshes["RaMesh"]
    e = derr("drv_call", None, ref="data/objects", method="new", args=["RaOb3", "MATERIAL:NoSuchMat"])
    assert "Traceback" not in json.dumps(e), A(e)
    for n in ("RaOb", "RaOb2"):
        bpy.data.objects.remove(bpy.data.objects[n], do_unlink=True)
    bpy.data.meshes.remove(bpy.data.meshes["RaMesh"])
    return "drv_call pointer args resolve by IDTYPE:Name and #uid; a bad ref is a clean error (RA-12)"


step("DRV-RA12 drv_call pointer args via _drv_pointer_value (RA-12)", d_ra12, tag="DRV")


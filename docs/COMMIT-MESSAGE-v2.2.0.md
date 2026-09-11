2.2.0: Blender 5.2 LTS migration, settings/save/load, rigging/animation

Three versions in one commit, built and verified on Blender 4.3.2 and 5.2.1 LTS
by team nuzukm on 2026-09-11 (branch blender-5.2; main stays at v1.6.0).

2.0.0  Blender 5.2 LTS migration
  - Five 5.x breaks fixed by try-assign, never by version compares: EEVEE
    engine id, Scene.node_tree removal (compositor node group), boolean solver
    FAST -> FLOAT (+ MANIFOLD on 5.2), ImageFormatSettings.media_type before
    file_format, ShaderNodeSeparateRGB -> ShaderNodeSeparateColor.
  - HDRI temp-file leak, extrude_faces interior cap, active/selection restore
    (_selection_scope), portable blender-<ver>-windows-x64 folder detection.
  - 92 tools, names and input schemas identical to v1.6.0.

2.1.0  Settings, save and load (56 new tools -> 148)
  - Add-on: socket server autostarts (preference), binds 127.0.0.1, port and
    API keys move from Scene properties to AddonPreferences (one-time migration
    on load; legacy Scene props hidden until 2.3.0), get_version /
    ensure_server_running, PROTOCOL = 1.
  - Server: settings.py + ~/.blender_mcp/settings.json (env > file > defaults,
    BLENDER_MCP_SETTINGS_DIR), presets_default.json, BlenderCommandError,
    one-time version-mismatch notice, get_version / get/set_server_settings.
  - File lifecycle, generic + typed settings, snapshots, presets, project
    profile, add-ons / workspaces / preferences (consent rule), session state.

2.2.0  Rigging and animation Tier 1 (21 new tools -> 169)
  - Armatures and bones (names only, never EditBone refs), skinning and
    weights, pose and constraints, set_keyframes / get_animation_info /
    playblast / bake_action; rig-aware export_object, add_keyframe(bone),
    capture overlays, parent_object(BONE), import_file (disable_bone_shape).
  - 5.x slotted actions bound explicitly; Bone.select vs PoseBone.select.
  - A1.2 (live pass on 5.2.1 GUI): 1x1 capture fix, even playblast dims,
    camera view on OpenGL playblast, window fallback after open_mainfile,
    cleared selection flags. A1.3: find_unweighted_vertices restores the
    select flags. F6: RLock around send_command + one resend only before the
    request reached the add-on.
  - Tier 2 (28 wrappers) staged, not shipped (docs/PLAN.md 10.4).

Files: addon.py, src/blender_mcp/server.py, src/blender_mcp/__init__.py,
src/blender_mcp/settings.py (new), src/blender_mcp/presets_default.json (new),
pyproject.toml, README.md, TOOLS.md, docs/ (request docs with facts verified on
both versions, MIGRATION-blender-5.2.md, PLAN.md, HANDOFF-2026-09-11.md,
LIVE-TEST-PLAN.md, LIVE-TEST-REPORT-2026-09-11.md, release-notes/, apichecks/),
tests/ (headless harness, venv suites, baselines, reports),
TERMS_AND_CONDITIONS.md (fork terms, pre-existing working-tree change).
Not part of this commit: pb_1..4.png (stray test artefacts, removed).

Evidence at the final pair (addon 0eb7ec4c / server 2ddfc98a /
__init__ f76c47d1 / settings 2bbc200b / presets 0a58e8c3): headless harness
186/186 on 4.3.2 and 186/186 on 5.2.1 (158 handlers), venv 69/69 (5 new F6
tests), tools/list identical to
tests/baseline_tools_2.2.txt (169). Live pass on 5.2.1 GUI:
docs/LIVE-TEST-REPORT-2026-09-11.md.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01SLqqLddfVSsJSaEHHUQXeF

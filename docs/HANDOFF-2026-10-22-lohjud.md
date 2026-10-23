# HANDOFF, team lohjud (Blender MCP 2.4.0 stage 1), 2026-10-22 ~08:30Z (usage-limit pause #2)

Team PAUSED (team_stop end=False); team_resume(team_id=lohjud, member=...) reopens each conversation.
Resume ONLY Margaret first (her verify is the critical path), Barbara + Grace for their rows after, Linus only on a red.

## State on disk (UNCOMMITTED on blender-5.2, nothing deployed/launched)
- REOPEN PAIR (not yet re-frozen): addon.py 2a97736c (F-B2-4/6/7, Linus, Lead re-review empty) / src/blender_mcp/server.py
  78df0099 (F-B2-5, Grace, Lead re-review empty; Grace to confirm final on card b2-d) / tests/b2_steps.py ba354830 /
  tests/drv_steps.py a45e37c4 / tests/test_b2_server.py 7d84e42b (FakeConn close_blender case STILL PENDING, Margaret).
- Linus's clean run on that pair: 253/253 both installs EXIT 0, settings --check match both (card b2-f4, Landed).
  Margaret's red proof at a scratch ee9b9dc0: DRV-12c + B2-E4 red, nothing else.
- Contract of record B2.22 = docs/PLAN.md f3ba0046 (ratified, no deltas). Rulings R-B2-1..46 + landing log:
  docs/LEAD-RULINGS-B2.md. Live report v1: docs/LIVE-TEST-REPORT-2026-10-22.md e289f522.
- Mutants for the re-probe: scratchpad (session 3f74970e...)\recon\f4\mut_f4 b337e690 / mut_f6 11b306b1 / mut_f7 773995d0.
- Docs landed at G4: TOOLS.md ad56d6c0, README.md df3cbe13, docs/release-notes/v2.4.0.md b83789c0 (Grace has a
  "Live-test fixes (B2-G5)" block + the TOOLS.md rna_set E21 sentence in flight; release-notes hashes are placeholders).

## Remaining sequence (room post m_baff103951ecdf9b68ed item 4-5)
1. Margaret: (a) verify from disk 253/253 both installs at the pair above; (b) FakeConn close_blender case into
   test_b2_server.py (red at an a2cdd77f-shaped scratch, green at 78df0099), venv 131 passed 0 skipped;
   (c) tests/dump_tools_list.py diff vs tests/baselines/tools_list_2.4.json IDENTICAL; (d) settings --check both;
   (e) RE-FREEZE comment on card b2-live naming all five hashes; (f) live re-probe on restarted GUIs (port 9877, dev
   tree, never the user's sessions): 4.3.2 three captures with view fields equal before/after + Timbermesh file +
   frame_current unchanged; both builds execute_blender_code(dict) + close_blender(force=True) on the unmanaged
   instance -> "asked to quit" and the process exits; mut_f6 as the live red; cleanup; report v2 with both hashes.
2. Lead: call RE-FREEZE + B2-G5 PASSED in the room; tick k1i8 on b2-f4 and k1i16 on b2-live.
3. Barbara: 13.11 rows (landings, re-freeze, G5 PASSED, completion); b2-p -> Done.
4. Grace: fill the release-notes hashes (addon 2a97736c, server 78df0099 or final), land on b2-d.
5. Lead: completion post; docs/LEAD-COMPLETION-B2.md final (hashes); MemPalace decisions drawer (db blender-mcp);
   project memory; skill section 26 G5/completion line; collect Margaret's/Barbara's AAR addenda; END the team.
6. STOP. Stage 2 (UV and texturing, 2.5.0) only on the user's explicit go.

## User decisions pending
- 2.3.0 bytes never committed and overwritten: 2.3.0 + 2.4.0 = ONE commit (both release notes ride).
- Commit / deploy to live targets / push: the user's call.

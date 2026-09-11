# Feature request: new-version notification for agents

Status: OPEN, requested 2026-09-09
Target version: 1.7.x (small, server-side, no new addon handlers except `get_version`)
Baseline: v1.6.0, 92 tools (commit `ef0ff7d`); GitHub latest release `v1.6.0`
Architecture, ripple points and deploy: `FEATURE-REQUEST-rigging-and-animation.md`
sections 0, 1, 4, 7. Not repeated here.

## 0. Scope and why

An agent using this MCP has no way to know that the copy it is talking to is behind the
latest release, and no way to know that the Blender add-on and the Python server are
different versions (they share a wire protocol and must be deployed together). The
request: on first use, tell the agent ONCE which version is installed and which is the
latest release on GitHub, with the link. Never nag, never block a tool call, never fail a
tool call because the network was down.

Assumption (the ask said "only notify once"): once per NEW RELEASE, persisted across
server restarts, not once per session. A fresh session after the user upgrades says
nothing; a fresh session while still behind says nothing again for the same release. This
is configurable (`once_per`), see section 2.3.

Build what is in this document. If something is wrong, say so in the report and continue.

## 1. Facts verified 2026-09-09 (do not re-derive)

- Repository: `git@github.com:naab007/blender_mcp.git`, public. Releases API:
  `GET https://api.github.com/repos/naab007/blender_mcp/releases/latest` returns
  `tag_name` (`v1.6.0`), `name`, `html_url`, `published_at` (ISO 8601 Z), `prerelease`,
  `draft`, `body` (Markdown, 2,090 chars for v1.6.0), `assets[].name` (`addon.py`),
  `assets[].browser_download_url`. Unauthenticated limit is 60 requests per hour per IP;
  `X-RateLimit-Remaining` is in the response headers. `/releases/latest` excludes
  pre-releases and drafts by definition; `/releases?per_page=5` is needed only when
  `include_prereleases` is on.
- The local clone has NO tags (`git tag` is empty; the v1.6.0 tag lives on GitHub only).
  Do not derive the installed version from git.
- Version sources today are THREE and they disagree:
  - `pyproject.toml` `version = "1.6.0"` (authoritative)
  - `addon.py` `bl_info["version"] = (1, 6, 0)`
  - `src/blender_mcp/__init__.py` `__version__ = "0.1.0"` (stale, never bumped)
  - `importlib.metadata.version("blender-mcp")` reports `1.5.5` in the dev venv (the
    editable install's metadata is regenerated only on reinstall, and the memory note
    records a stale 1.5.6 shadowing the live venv once already). NEVER use metadata for
    the installed version.
- FastMCP (`mcp[cli]>=1.3.0` installed) accepts `instructions=` in `FastMCP.__init__`
  (verified). The server is built as `FastMCP("BlenderMCP", lifespan=server_lifespan)`
  with no instructions today. Instructions are sent in the initialize response and shown
  to the agent at session start (the AgentBrowser MCP uses this and it appears in the
  agent's system prompt).
- `requests` 2.33.1 is already a dependency. Use it with `timeout=(3, 5)`, a User-Agent
  that identifies the project and carries the agents' contact address
  (`blender-mcp/<version> (+https://github.com/naab007/blender_mcp; naabins.agents@gmail.com)`),
  and `verify=True`. No new dependency.
- Existing server-side state lives in module globals and `bpy.app.driver_namespace` on
  the addon side. The settings request introduces `~/.blender_mcp/settings.json`; this
  request stores its state there (create the file if the settings request has not shipped
  yet; the schema below is additive).

## 2. Design

### 2.1 Version handling (fix first)

Status 2026-09-11: item 1 built in 2.0.0 (`tests/test_version_sync.py`, all three sources
bumped together), item 2 built in 2.1.0 B0-A L1 (add-on hash `39162cbf`: `get_version`
command with keys `addon`, `protocol`, `blender`, `python`, `addon_file`, `background`;
`PROTOCOL = 1`, the first numbered protocol, bumped by any later wire change); item 3 is
the server side of B0 (pending its landing). Sections 2.2-2.5 (the check itself) are B8+.

1. Make `src/blender_mcp/__init__.py` `__version__` the single runtime source for the
   server, and add a `tests/test_version_sync.py` (or a check in the deploy script) that
   fails when `__version__`, `pyproject.toml` and `addon.py` `bl_info["version"]` differ.
   Bump all three together from now on (the dual-source rule in the auto-memory extends to
   three files; record that).
2. Add addon command `get_version` → `{"addon": "1.6.0", "blender": "4.3.2",
   "python": "3.11.9", "protocol": 1}`. `protocol` is a new integer in the addon, bumped
   whenever the wire format changes (the rigging and texturing requests change it); the
   server carries its own `PROTOCOL` constant.
3. Server tool `get_version` → server version, addon version (via the command, or
   `"unreachable"`), Blender version, protocol numbers, install path of `server.py`
   (catches the stale-site-packages shadowing problem), settings file path, and the last
   update-check result if any.

### 2.2 The check

- Runs in the background at server start: `server_lifespan` schedules
  `asyncio.create_task(_update_check())`, which calls the GitHub API through
  `asyncio.to_thread` so the stdio loop is never blocked. It never raises; any failure is
  logged at INFO and the check is marked `unavailable` for this session.
- Cached: the response (`tag_name, name, html_url, published_at, body[:2000],
  assets, checked_at`) is written to `settings.json` under `update_check.latest`. A check
  is skipped when `checked_at` is younger than `update_check.ttl_hours` (default 6), so
  an agent that opens ten sessions a day costs one API call.
- Comparison: parse `tag_name` by stripping a leading `v` and splitting on `.` into an
  integer tuple; compare with `__version__` the same way. Pre-release suffixes
  (`1.7.0-rc1`) sort below the plain version. Do not add `packaging` as a dependency for
  this.
- Also compares addon vs server version and protocol numbers on the first successful
  Blender connection (not at startup, Blender may not be running yet).

### 2.3 The notification

Delivered through two channels, both derived from the same state:

1. **Instructions at session start** (static, always present): one line telling the
   agent the server version and that update notices arrive in tool replies:
   `BlenderMCP server 1.6.0. If a newer release exists you will be told once in a tool
   reply; call get_version or check_for_updates for details.`
2. **A one-time prefix on a tool reply** (dynamic): the first tool call that completes
   AFTER the check has finished and found a newer release, and whose result is a string
   (never an Image; wait for the next string result), gets this prepended:

   ```
   [blender-mcp update] Installed 1.6.0 (addon 1.6.0). Latest release v1.7.0 "rigging
   and animation" published 2026-09-20: https://github.com/naab007/blender_mcp/releases/tag/v1.7.0
   Deploy per project_blender_mcp.md (server + addon together). This notice is shown once per release.
   ---
   <original tool reply>
   ```

   A second, independent one-time prefix covers a mismatch:
   `[blender-mcp mismatch] Server 1.7.0 / protocol 2 but the Blender add-on reports 1.6.0 /
   protocol 1. Wire formats differ; deploy addon.py and cycle the add-on before continuing.`
   The mismatch notice is once per SESSION (it is a live fault, not news), and
   `get_version` always includes it.

   "Once" is persisted as `update_check.notified_tag` in `settings.json`; the prefix is
   emitted only when `latest.tag_name != notified_tag` and the installed version is older,
   then `notified_tag` is written immediately (before the reply is returned, so a crash
   cannot cause a repeat). In-process, a module flag prevents a race between two tool calls.

Implementation of the prefix without touching 92 tool bodies: wrap `mcp.tool` once at
the top of `server.py`, before any `@mcp.tool()` line:

```python
_orig_tool = mcp.tool
def _tool_with_notices(*a, **k):
    deco = _orig_tool(*a, **k)
    def register(fn):
        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapper(*fa, **fk):
                return _attach_notice(await fn(*fa, **fk))
        else:
            @functools.wraps(fn)
            def wrapper(*fa, **fk):
                return _attach_notice(fn(*fa, **fk))
        return deco(wrapper)
    return register
mcp.tool = _tool_with_notices
```

`functools.wraps` copies `__annotations__` and sets `__wrapped__`, and `inspect.signature`
follows `__wrapped__`, so FastMCP's schema generation is unchanged. VERIFY this by
diffing `tools/list` output before and after (names, descriptions, input schemas must be
byte-identical). `_attach_notice(result)` returns `result` untouched unless it is a `str`
and a notice is pending.

### 2.4 Settings and controls

`settings.json` additions (all optional, defaults shown):

```json
"update_check": {
  "enabled": true,
  "once_per": "RELEASE",          // RELEASE | SESSION | NEVER_NOTIFY (check only, get_version shows it)
  "ttl_hours": 6,
  "include_prereleases": false,
  "repo": "naab007/blender_mcp",
  "latest": { "tag_name": "...", "name": "...", "html_url": "...", "published_at": "...", "checked_at": "..." },
  "notified_tag": null
}
```

Environment overrides: `BLENDER_MCP_NO_UPDATE_CHECK=1` disables the network call
entirely (CI, offline, air-gapped). `BLENDER_MCP_UPDATE_REPO` overrides the repo.

### 2.5 Tools

| Tool | Parameters | Behaviour and reply |
|---|---|---|
| `get_version` | | See 2.1.3. Always available, never touches the network. |
| `check_for_updates` | `force=False`, `include_prereleases=None`, `show_notes=False` | Runs the check now (ignoring the TTL when `force`), returns installed vs latest, `is_outdated`, release name, date, URL, asset names, and the release notes (first 2,000 chars, Markdown) when `show_notes`. Does NOT change `notified_tag` unless `acknowledge=True`. Network failure returns `available: false` with the reason, exit status success. |
| `acknowledge_update` | `tag=None` (latest if None) | Sets `notified_tag` so the prefix stops for that release. For the case where the user says "I know, later". |
| `set_update_check` | `enabled=None`, `once_per=None`, `ttl_hours=None`, `include_prereleases=None` | Writes the settings block. Persisted immediately (server-side file, no Blender preferences involved, so no consent flag needed). |

## 3. Ripple points beyond the standard list

- `src/blender_mcp/__init__.py` version; `pyproject.toml`; `addon.py` `bl_info` and the
  new `PROTOCOL` constant + `get_version` handler in `extended_handlers`.
- `server.py`: `FastMCP(..., instructions=...)`, the `mcp.tool` wrapper (must be defined
  before the first `@mcp.tool()`), lifespan task, settings loader shared with the settings
  request.
- `README.md`: a short "Update notifications" paragraph with the env var to disable.
- `TOOLS.md`: new section "Version & Updates" (4 tools).
- Auto-memory `project_blender_mcp.md`: the three-file version rule and the settings key.
- Release process note in `README.md`: the notice quotes `tag_name` and `name`, so
  releases must be tagged `vX.Y.Z` and given a short descriptive name; attach `addon.py`
  as an asset (already the practice for v1.6.0).

## 4. Testing (required before you report done)

Unit tests with the network mocked (`requests.get` patched), no Blender needed:

1. Version parse: `v1.6.0` > `1.5.9`, `1.7.0-rc1` < `1.7.0`, `v1.10.0` > `v1.9.9`,
   malformed tag → `available: false`, no crash.
2. Fresh settings, mocked latest `v1.7.0`, installed `1.6.0`: the first string-returning
   tool reply carries the prefix, the second does not, `notified_tag == "v1.7.0"` on disk.
3. Restart the module (reload) with the same settings: no prefix (once per release).
4. Mocked latest `v1.8.0`: prefix again, exactly once.
5. `once_per = "SESSION"`: prefix once per process even with `notified_tag` set.
6. Image-returning tool called first: no prefix, no crash, the next string tool gets it.
7. Network failure (ConnectionError, timeout, HTTP 403 rate-limited, invalid JSON): tool
   replies unaffected, `check_for_updates` reports `available: false` with the reason.
8. TTL: two server starts within 6 h make ONE mocked request; `force=True` makes another.
9. `BLENDER_MCP_NO_UPDATE_CHECK=1`: `requests.get` is never called; `get_version` works.
10. `tools/list` before and after the wrapper: identical names, descriptions and
    `inputSchema` for all 92 tools (assert with a JSON diff).
11. Async tool (`start_blender` path with the process mocked) goes through the wrapper
    without breaking `await`.
12. Version sync test fails when any of the three version sources is edited alone.
13. Mismatch: addon `get_version` mocked to `1.5.0/protocol 1` against server
    `1.7.0/protocol 2` → mismatch prefix once, and present in every `get_version` reply.

Live check after deploy: start a session, call `get_scene_info`, confirm the notice
appears once and the instructions line is present in the session's MCP server
instructions.

Report PASS / FAIL per step with the error text. Do not weaken an assertion to make it pass.

## 5. Deploy

Identical to section 7 of the rigging request. This feature changes `server.py`,
`__init__.py`, `pyproject.toml` and `addon.py` (the `get_version` handler), so all four
go to every target and the add-on is cycled. Do not launch Blender or deploy until told.

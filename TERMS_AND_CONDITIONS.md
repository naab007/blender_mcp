# Blender MCP (extended fork) - Terms of Use

**Last Updated: September 2026**

---

## 1. About This Project

This is an independent, open-source fork of Blender MCP, maintained at
https://github.com/naab007/blender_mcp. It connects Blender to AI assistants through the
Model Context Protocol.

By using this software you agree to these terms. If you do not agree, do not use it.

---

## 2. No Data Collection

This fork collects nothing.

- There is no telemetry, analytics, usage reporting or crash reporting.
- No prompts, generated code, scene data, screenshots, files or metadata are sent to the
  maintainer or to any third party by this software.
- All communication happens between the MCP server and the Blender add-on on your own
  machine, over a loopback socket, unless you configure it otherwise.

The only network traffic this software initiates on its own is the optional download of
third-party dependencies you explicitly request (for example model weights for the
image-to-3D feature).

---

## 3. Third-Party Integrations

Some optional features talk to external services when, and only when, you enable them in
the add-on panel and supply your own credentials: PolyHaven, Sketchfab, Hyper3D (Rodin)
and Hunyuan3D. When you use one of these, data you send (search terms, prompts, images,
API keys) goes directly from your machine to that service under that service's own terms
and privacy policy. This project has no access to it and no agreement with those services.

The AI assistant you connect to this software (for example a Claude client) is likewise
governed by its provider's terms. This project does not see or store that traffic.

---

## 4. Ownership

You own everything.

- You retain all rights to your prompts, your Blender files, your models, textures,
  renders, animations and any other content you create or process with this software.
- You retain all rights to any code, scripts or data generated in response to your
  prompts while using this software.
- You grant the maintainer no license of any kind to any of the above. Nothing you do
  with this software transfers, assigns or licenses any right to anyone.

The source code of this software is licensed separately under the MIT License in the
`LICENSE` file. That license covers the software only, never your content.

---

## 5. No Warranty

THE SOFTWARE IS PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED.

There is no guarantee that the software will work correctly, that AI-generated code will
be safe or functional, or that any operation it performs on your files is reversible.
Review generated code before running it and keep backups of your work.

---

## 6. Limitation of Liability

TO THE MAXIMUM EXTENT PERMITTED BY LAW, THE MAINTAINER IS NOT LIABLE FOR ANY DAMAGES
ARISING FROM YOUR USE OF THIS SOFTWARE, INCLUDING LOSS OF DATA OR WORK.

This is a free, open-source project maintained in spare time. Use at your own risk.

---

## 7. Changes

These terms may be updated. Changes are published in the repository. Continued use after a
change means you accept the updated terms.

---

## 8. Contact

Questions or issues: open an issue at https://github.com/naab007/blender_mcp.

---

*This project is an independent fork and is not affiliated with the Blender Foundation or
with the upstream Blender MCP author.*

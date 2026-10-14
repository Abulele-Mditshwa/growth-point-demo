"""
Build a Word (.docx) version of SOLUTION-OVERVIEW.md.

Word cannot render Mermaid, so this script:
  1. Extracts every ```mermaid block from the markdown.
  2. Renders each one to a high-resolution PNG using Mermaid in headless
     Chromium (via Playwright, already installed for UI verification).
  3. Writes a build copy of the markdown with each Mermaid block replaced by an
     image reference.
  4. Converts that copy to .docx with pandoc.

Usage:
    .venv/bin/python docs/build_docx.py
"""

import base64
import re
import subprocess
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
SRC_MD = DOCS / "SOLUTION-OVERVIEW.md"
BUILD_DIR = DOCS / "_build"
IMG_DIR = BUILD_DIR / "diagrams"
BUILD_MD = BUILD_DIR / "SOLUTION-OVERVIEW.build.md"
OUT_DOCX = DOCS / "Growthpoint-Knowledge-Assistant-Solution-Overview.docx"

MERMAID_BLOCK = re.compile(r"```mermaid\n(.*?)\n```", re.DOTALL)

# Rendered via the Mermaid ESM build pinned to a known-good version.
PAGE_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html,body {{ margin:0; padding:24px; background:#ffffff;
               font-family:'Helvetica Neue',Arial,sans-serif; }}
  #c {{ display:inline-block; }}
</style>
</head>
<body>
<div id="c" class="mermaid">{graph}</div>
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  mermaid.initialize({{
      startOnLoad: false,
      theme: 'base',
      themeVariables: {{
          fontFamily: "'Helvetica Neue',Arial,sans-serif",
          fontSize: '15px',
          primaryColor: '#F7E9F1',
          primaryTextColor: '#470635',
          primaryBorderColor: '#470635',
          lineColor: '#7A6B74',
          textColor: '#2E0422'
      }},
      flowchart: {{ htmlLabels: true, curve: 'basis' }}
  }});
  await mermaid.run({{ querySelector: '#c' }});
  window.__done = true;
</script>
</body></html>
"""


def render_diagrams(graphs):
    """Render each mermaid definition to a PNG. Returns list of Paths."""
    from playwright.sync_api import sync_playwright

    IMG_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for i, graph in enumerate(graphs, 1):
            # deviceScaleFactor=2 gives a crisp image when scaled in Word.
            page = browser.new_page(
                viewport={"width": 1600, "height": 1200}, device_scale_factor=2
            )
            html = PAGE_TEMPLATE.format(graph=graph)
            page.set_content(html)
            try:
                page.wait_for_function("window.__done === true", timeout=30000)
            except Exception as e:  # noqa: BLE001
                print(f"  ! diagram {i} did not signal completion: {e}")
            page.wait_for_timeout(600)

            target = page.query_selector("#c svg") or page.query_selector("#c")
            dest = IMG_DIR / f"diagram-{i:02d}.png"
            target.screenshot(path=str(dest))
            size = dest.stat().st_size
            print(f"  rendered diagram {i} -> {dest.name} ({size:,} bytes)")
            if size < 2000:
                print(f"  ! diagram {i} looks suspiciously small; check syntax")
            out.append(dest)
            page.close()
        browser.close()
    return out


def main() -> int:
    md = SRC_MD.read_text(encoding="utf-8")
    graphs = MERMAID_BLOCK.findall(md)
    print(f"Found {len(graphs)} mermaid diagram(s) in {SRC_MD.name}")
    if not graphs:
        return 1

    images = render_diagrams(graphs)

    # Replace each mermaid block, in order, with an image reference + caption.
    captions = [
        "Figure 1 — AWS ingestion pipeline (offline)",
        "Figure 2 — AWS query pipeline (runtime)",
        "Figure 3 — AWS end-to-end sequence",
        "Figure 4 — Azure ingestion pipeline (offline)",
        "Figure 5 — Azure query pipeline (runtime)",
        "Figure 6 — Azure sequence with authenticated identity",
    ]

    counter = {"i": 0}

    def sub(_match):
        i = counter["i"]
        counter["i"] += 1
        rel = images[i].relative_to(BUILD_DIR).as_posix()
        cap = captions[i] if i < len(captions) else f"Figure {i + 1}"
        return f"![{cap}]({rel})\n\n*{cap}*"

    build_md = MERMAID_BLOCK.sub(sub, md)

    # The ASCII pipeline sketches are fine in Word as monospace code blocks.
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_MD.write_text(build_md, encoding="utf-8")
    print(f"Wrote {BUILD_MD.relative_to(ROOT)}")

    # Convert with pandoc, resolving image paths relative to the build dir.
    cmd = [
        "pandoc",
        BUILD_MD.name,
        "-o", str(OUT_DOCX),
        "--from", "gfm",
        "--toc",
        "--toc-depth=2",
        "--resource-path", ".",
        "--metadata", "title=Growthpoint Knowledge Assistant — Solution Overview",
    ]
    print("Running pandoc...")
    res = subprocess.run(cmd, cwd=BUILD_DIR, capture_output=True, text=True)
    if res.returncode != 0:
        print("pandoc failed:\n", res.stderr)
        return res.returncode
    print(f"Created {OUT_DOCX.relative_to(ROOT)} "
          f"({OUT_DOCX.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

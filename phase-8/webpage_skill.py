"""
Parashurama webpage generation skill — code-only, self-contained HTML.

Parashurama writes Python code that generates a self-contained HTML file.
The executor runs it in a sandbox and returns a URL served by the local
Avatara static file server.

CDN libraries usable with zero install (embed as <script src="..."> tags):
  Three.js  — https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.min.js
  D3.js     — https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js
  Chart.js  — https://cdn.jsdelivr.net/npm/chart.js
  p5.js     — https://cdn.jsdelivr.net/npm/p5/lib/p5.min.js
  GSAP      — https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js
  Anime.js  — https://cdn.jsdelivr.net/npm/animejs@3/lib/anime.min.js
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PHASE7 = Path(__file__).parent.parent / "phase-7"
sys.path.insert(0, str(_PHASE7))
from executor import execute_code

_SERVER_MEDIA_BASE = os.environ.get("MEDIA_URL_BASE", "http://localhost:8000/media")


def create_webpage(
    code: str,
    output_filename: str = "index.html",
) -> dict:
    """Generate a self-contained HTML page by executing Python code in a sandbox.

    Parashurama must pass complete Python code that:
    - Builds a full HTML string (inline CSS + JS, or CDN <script> tags)
    - Writes it to: os.path.join(OUTPUT_DIR, "index.html")
    - Requires no local file dependencies — everything self-contained or from CDN

    The returned URL is served by the local Avatara server and can be opened
    in any browser tab. Works fully offline for inline content; CDN scripts
    require an internet connection.

    Recommended CDN libraries (use these in <script src="..."> tags):
      Three.js 3D:    https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.min.js
      D3 data viz:    https://cdn.jsdelivr.net/npm/d3@7/dist/d3.min.js
      Chart.js:       https://cdn.jsdelivr.net/npm/chart.js
      p5.js art:      https://cdn.jsdelivr.net/npm/p5/lib/p5.min.js
      GSAP animation: https://cdn.jsdelivr.net/npm/gsap@3/dist/gsap.min.js
      Anime.js:       https://cdn.jsdelivr.net/npm/animejs@3/lib/anime.min.js

    Example — rotating Three.js icosahedron:
      html = \"\"\"<!DOCTYPE html><html><head>
      <style>body{margin:0;overflow:hidden;background:#0a0a0f;}</style></head>
      <body>
      <script src="https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.min.js"></script>
      <script>
        const scene = new THREE.Scene();
        const cam = new THREE.PerspectiveCamera(75, innerWidth/innerHeight, 0.1, 1000);
        const renderer = new THREE.WebGLRenderer({antialias:true});
        renderer.setSize(innerWidth, innerHeight);
        document.body.appendChild(renderer.domElement);
        const mesh = new THREE.Mesh(
          new THREE.IcosahedronGeometry(1, 2),
          new THREE.MeshNormalMaterial({wireframe:true})
        );
        scene.add(mesh); cam.position.z = 3;
        (function loop(){ requestAnimationFrame(loop);
          mesh.rotation.x += 0.008; mesh.rotation.y += 0.012;
          renderer.render(scene, cam); })();
        window.addEventListener('resize', () => {
          cam.aspect = innerWidth/innerHeight; cam.updateProjectionMatrix();
          renderer.setSize(innerWidth, innerHeight); });
      </script></body></html>\"\"\"
      with open(os.path.join(OUTPUT_DIR, "index.html"), "w") as f:
          f.write(html)

    Returns a dict with status, url (open in browser), file_path, and message.
    """
    if not code or not code.strip():
        return {
            "status":  "error",
            "message": "code parameter is required — pass Python code that writes an HTML file to OUTPUT_DIR",
            "url":     "",
        }

    result = execute_code(code)

    html_files = [f for f in result.get("output_files", []) if f.endswith(".html")]

    if result["status"] == "ok" and html_files:
        out_path = html_files[0]
        rel      = Path(out_path).name
        run_id   = result["run_id"]
        url      = f"{_SERVER_MEDIA_BASE}/{run_id}/{rel}"
        return {
            "status":    "ok",
            "url":       url,
            "file_path": out_path,
            "message":   (
                f"Page generated in {result['duration_s']}s. "
                f"Open {url} in a browser tab."
            ),
        }

    stderr_snippet = result["stderr"][:300].strip()
    return {
        "status":  result["status"],
        "url":     "",
        "message": (
            f"Execution failed ({result['status']}). "
            f"Error: {stderr_snippet}. "
            "Fix the code and try once more — if it fails again, explain the limitation to the user."
        ),
    }

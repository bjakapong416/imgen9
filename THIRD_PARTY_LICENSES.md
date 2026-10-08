# Third-party software

This project's own code is licensed under the GNU AGPL-3.0-or-later (see `LICENSE`), except the
Blender scripts in `blender/`, which are GPL-3.0-or-later (see `blender/LICENSE`) because they run
inside Blender through its GPL `bpy` API.

It depends on the following software, each under its own licence. Nothing here is bundled in the
repository; `pip install -r requirements.txt` fetches it. If you redistribute a build that bundles
these packages (for example a one-click installer), include their licence texts and any `NOTICE`
files they ship.

| Package | Licence | Used for |
|---|---|---|
| [FastAPI](https://github.com/fastapi/fastapi) | MIT | web server API |
| [Starlette](https://github.com/encode/starlette) | BSD-3-Clause | web framework under FastAPI |
| [Pydantic](https://github.com/pydantic/pydantic) | MIT | request/response validation |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | HTTP server |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 | file uploads |
| [Pillow](https://github.com/python-pillow/Pillow) | MIT-CMU | image processing |
| [NumPy](https://github.com/numpy/numpy) | BSD-3-Clause (and bundled permissive licences) | image arrays |
| [SciPy](https://github.com/scipy/scipy) | BSD-3-Clause | sheet splitting (labelling) |
| [rembg](https://github.com/danielgatis/rembg) | MIT | background removal |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) | MIT | runs the background-removal model |
| [pymatting](https://github.com/pymatting/pymatting), [scikit-image](https://github.com/scikit-image/scikit-image), [pooch](https://github.com/fatiando/pooch), [jsonschema](https://github.com/python-jsonschema/jsonschema), [tqdm](https://github.com/tqdm/tqdm) | MIT / BSD-3-Clause / MPL-2.0 AND MIT | pulled in by rembg |
| [anthropic](https://github.com/anthropics/anthropic-sdk-python) | MIT | optional: image classification with your own API key |

These packages install their own dependencies too (for example numba and llvmlite through pymatting, and the
`uvicorn[standard]` extras); each ships its own licence file inside the installed package.

## Models downloaded on first run

rembg downloads its ONNX model the first time it removes a background (about 180 MB, cached in
`~/.rembg/models` or `U2NET_HOME`). The default is `isnet-general-use` (IS-Net / DIS); `u2net`, `u2netp` and
`isnet-anime` can be chosen with `REMBG_MODEL`. These models are published by their authors under the Apache-2.0
licence. Check the model's own page before relying on that for your use.

## Optional tools you install yourself

- **Blender** (GPL): renders the optional 3D route. Not bundled.
- **Hunyuan3D-2** (Tencent Hunyuan Community License): optional local image-to-3D, off by default
  (`AUTO_LOCAL_3D`). Not bundled. Its licence grants no rights in the European Union, the United
  Kingdom or South Korea, and limits large commercial products. Read it before installing.

## File formats

The tool reads and writes the `.spr`/`.act` sprite format for compatibility only. It includes no
files from any game and is not affiliated with any game publisher.

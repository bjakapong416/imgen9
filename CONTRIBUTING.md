# Contributing

Thanks for helping. Bug reports, ideas and pull requests are welcome.

## Before you open a pull request

1. **Sign the Contributor License Agreement (CLA) below.** Pull requests can't be merged without it.
   You sign it by adding this line to your first pull request's description:

   > I have read and agree to the Contributor License Agreement in CONTRIBUTING.md.

2. Only contribute work you made yourself and have the right to give. Never commit images, sprites
   or other files from games or artists you don't own.
3. Keep image prompts free of other games' and artists' names; describe the look in plain words.

## Why a CLA

The project is open source under the AGPL-3.0. The maintainer also wants to be able to offer it
under other terms (for example a commercial licence or a paid build) to keep the project funded.
That is only possible if every contribution can be relicensed, which is what the CLA allows. You
keep the copyright in your work.

## Contributor License Agreement

> **Draft. Have it reviewed by a lawyer before accepting outside contributions.**

By submitting a contribution (code, documentation, images or other material) to this project, you
agree to the following with the project maintainer ("the Maintainer"):

1. **Your copyright stays yours.** You keep all rights in your contribution.
2. **Copyright licence.** You grant the Maintainer and anyone who receives software from the
   Maintainer a perpetual, worldwide, non-exclusive, royalty-free, irrevocable licence to use,
   reproduce, modify, prepare derivative works of, publicly display, distribute and sublicense your
   contribution, and to license it under any terms, including the AGPL-3.0, other open-source
   licences and proprietary or commercial licences.
3. **Patent licence.** You grant the same parties a perpetual, worldwide, non-exclusive,
   royalty-free, irrevocable patent licence to make, use, sell, offer to sell, import and otherwise
   transfer your contribution, for any patent claims you can license that your contribution
   infringes on its own or combined with the project.
4. **You have the right to give this.** The contribution is your original work, or you have the
   right to submit it under these terms. If your employer has rights in it, you have their
   permission. You will say clearly if any part comes from someone else and under what licence.
5. **No obligation.** The Maintainer doesn't have to use your contribution. It is provided "as is",
   without warranty.

Agreeing electronically, as described above, is intended to be a binding signature.

## Running tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q            # the whole suite takes a few seconds
python -m tools.i18n_check     # every UI string has a Thai translation
```

The tests build their inputs with Pillow (synthetic figures and sheets) and point the server at
temporary folders, so they need no network, no image AI and no game files. They never run background
removal: `rembg` is blocked in `tests/conftest.py`, so a test that reaches it fails instead of
downloading a model. CI (`.github/workflows/ci.yml`) runs the same commands on Python 3.11, 3.12 and 3.13 (Linux), plus 3.12 on macOS and Windows,
and syntax-checks every file in `static/js` with `node --check`. A test marked `xfail` documents a
known bug; the marks are strict, so once the bug is fixed the test fails as XPASS until you remove the mark.

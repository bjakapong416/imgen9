# ImGen9 (character & monster sprite maker)

FastAPI + vanilla JS tool that turns one character concept image into a game-ready 8-direction 2D
sprite: `.spr`/`.act` files plus an atlas + JSON for web games (PixiJS/Phaser). Users run it on their
own PC. See README.md for layout and docs/ONE_IMAGE_TO_GAME.md for what is and isn't automated.
Personal, machine-specific notes go in `CLAUDE.local.md` (git-ignored).

## How it works

- **Main route is 2D** (`core/sprite2d.py`): the user's own image AI (Gemini, ChatGPT, ...) draws
  action strips from the tool's prompts; the tool never calls an image AI itself. One prompt draws
  every action as one even grid: 10 rows x 6 frames (each action a front 3/4 row then a back 3/4
  row). Attacks come in two kinds: physical (`attack`) and magic (`cast`, 2 more rows, so 12). The
  magic attack is on when the user ticks it in the sheets step, else when the classified animation
  plan has a `cast`; spell effects are never drawn into the character. AIs draw even grids
  reliably, so every action has 6 frames. Per-action and per-view prompts are for repairs.
- Each prompt has a matching stick-figure pose guide (`core/pose_guide.py`, 🦴 in the UI) that the
  user attaches with the character: AIs follow a pose they can see far better than a pose described
  in words. The sheet check flags walks whose legs don't alternate. The tool mirrors the two
  drawings (front 3/4, back 3/4) to 8 directions.
- **Weapons can be a separate layer** (`core/weapons.py`, the weapons card), so a game can swap them:
  with the layer on, every sprite prompt asks for an empty, gripping hand; weapons are drawn on their
  own (upright, handle down) and laid over each frame at the hand from the pose guide, with
  per-frame corrections in the hand editor. The build then also writes `<slug>_body` and
  `<slug>_weapon` `.spr/.act`. Weapon types (`weapons.TYPES`: staff, dagger, sword, spear, bow, gun,
  axe, mace) are grouped by attack style (swing / thrust / bow / gun): the character's weapon type
  sets the attack the prompts and pose guide ask for, so weapons of one style swap freely and another
  style needs a new attack drawing. A bow sits in the left hand. Each type has a stand-in sample
  picture (`weapons.sample`) for trying it before an AI draws the real weapon. The weapon layer shows
  only in the attack by default (`weapons.attack_only`): idle, walk, hurt and die are drawn with
  relaxed, empty hands. Playtest can swap a 2D character's weapons
  (`renders/<project>/<slug>/sprite/weapons/<id>.spr/.act`, one full sprite per weapon).
- **Skill effects are not part of this version.** They come back later as their own feature; until
  then the skill plan in the character profile is just notes.
- **Projects group characters** (`core/layout.py`, `projects/<project>/<character>/`,
  `renders/<project>/<character>/`): a project is one game or theme. Its `group.json` holds settings
  every character uses unless it sets its own (art style sentence added to every prompt, character
  style, sprite detail) and `style.png` an optional style reference image. Character ids stay unique
  across projects, so URLs and API paths use only the character id; `Layout` finds its project.
  The default project is `my_characters`.
- Optional and off by default: a local 3D route (Hunyuan3D + Blender, `AUTO_LOCAL_3D`). Avoid adding
  local AI models.

## Legal guard rails (keep these)

- **Prompts never name another game or artist.** Describe the look in plain words
  (`sprite2d.ART_STYLE`). Sprites made with the tool are meant to be used and sold.
- Never name another game, its publisher or its characters in code, UI, prompts or docs. `.spr`/`.act`
  is supported as a file format only; the frame-by-frame view (Sprite Inspector,
  opened from the result card) shows only sprites built here
  (`renders/<project>/<slug>/sprite/`, `own_` ids). Never add a way to load or ship another game's files.
- Never commit user projects, renders, screenshots of others' art, or keys. `projects/` and
  `renders/` are git-ignored except `renders/samples/sample_blockman`.
- Code is AGPL-3.0-or-later; `blender/` is GPL-3.0-or-later. Contributions need the CLA in
  CONTRIBUTING.md. List new dependencies in THIRD_PARTY_LICENSES.md.

## AI task: classify a character ("Classify character <slug>" / "จำแนกตัวละคร <slug>"; the older "Classify project" / "จำแนกโปรเจกต์" means the same)

An AI assistant in a chat (Claude Code; Gemini CLI and Codex read GEMINI.md / AGENTS.md, which point
here) can do the classification step instead of an API call:

1. `python -m tools.ai_tasks list` lists characters with `ai_pending`.
   `... show <slug>` prints the image paths, the current guess and the JSON schema.
2. Look at `projects/<project>/<slug>/concept.png` and every `views/*.png` next to it (`show` prints the paths) (front / front_3q /
   side / back) with the Read tool. If the user supplied reference sheets in chat, use them too.
3. Write a profile JSON that matches `core.classify.CreatureProfile`:
   - Names, breed, features, reasons and notes in the user's language. `name_en` is English
     snake_case.
   - `body_type` decides the rig: `humanoid` → Mixamo, anything else → Blender.
   - Always include the animations idle, walk, attack, hurt, die. Add extras that fit the role
     (a pet: sit, sleep, happy; a caster: cast).
   - 2–4 skills, each with the VFX it needs.
   - Notes: rigging path, which views the image doesn't show (back/sides), and risks.
   - `turnaround_prompt`: an English image-generation prompt for a front / 3/4 / side / back
     turnaround of this exact character. Use a pose suited to rigging (A-pose for humanoids, all four
     legs apart for quadrupeds), no weapon in hand, a plain background, and keep loose VFX (fire,
     sparks) out of the body. Don't name other games or artists.
   - `description_en`: one English sentence describing how the character looks (hair, outfit,
     colours). Every 2D action-strip prompt includes it, so it must be precise and stable. Leave out
     the held weapon: describe it in the weapons card (`weapons.desc_en` in project.json), which adds
     "holding …" or asks for empty hands when the weapon is its own layer. Leave out body
     proportions ("chibi", "big head", "short body"): the user picks those per character (or as a project default) in the
     character-info card (`sprite2d.BODY`; humanoids default to slender proportions).
4. Save the JSON to a scratch file, then run `... apply <slug> <file>`. It is validated against the
   schema and clears `ai_pending`. The browser shows the result after a refresh.

## AI task: art review ("Review character <slug>" / "ตรวจงานตัวละคร <slug>"; the older "Review project" / "ตรวจงานโปรเจกต์" means the same)

The automatic sheet check measures what numbers can (frame count, size wobble, legs, odd colours or
parts). The art review is the human-eye part, done by an AI assistant in chat (Claude Code, Gemini CLI, Codex):

1. `python -m tools.ai_tasks review <slug> <scratch file>.png` writes a contact sheet (reference views
   on top, then every drawn row, labelled with its key) and prints the description, body style and
   each row's automatic check.
2. Look at the sheet with the Read tool. Per row judge: facing (front 3/4 vs back 3/4 vs side),
   whether the motion reads (a wind-up and a strike, a flinch, legs alternating), anatomy, identity
   (same face, outfit, colours as the reference), and AI artefacts (motion blur, ghosting, cropped
   parts). Point to fixes the tool has: redraw one action with its 🦴 pose guide, or ✎ edit frames
   (delete, duplicate, pause, flip, replace a frame).
3. Write `{"summary": "...", "rows": {"<action>_<view>": {"verdict": "ok|fix|redraw", "notes": "..."}}}`
   in the user's language, then `python -m tools.ai_tasks apply-review <slug> <file>`. Badges (🎨)
   appear in the sheets card after a refresh. Delete the contact sheet if you wrote it into the project.

## Workflow rule

Each kind of image is dropped at its own step, never at an earlier one: a new character (a single image
or a turnaround sheet, split into views automatically) in the step-1 box on the left; more views of the
same character at step 3 (the views card, or step 3 in the right panel); AI-drawn action images in the
sheets card (step 4), right under the prompt that asked for them; weapon / headgear images in their
cards; a replacement concept with "Replace the concept image" in the character card. Every card that is
a step shows its number big (`.step-no`). Every later step consumes what earlier steps produced.
When the model is confirmed and already rigged and animated, the render starts on its own.

## Languages and the user guide

- English is the source text in code: `t("…")` in JS (`static/js/i18n.js`), `_("…")` in Python
  (`core/i18n.py`), `data-i18n` / `data-i18n-title` / `data-i18n-placeholder` in index.html. Named
  placeholders: `t("Got {n} frames", { n })`, `_("Got {n} frames", n=n)`; never `${}` / f-strings inside.
- Translations live in `static/locales/<code>.json` (English → translation), shared by browser and
  server. A new language = one JSON file + an entry in `LANGUAGES` in i18n.js.
- Server text is translated at request time (the `lang` cookie); module constants hold English.
  Keep API field names unchanged (e.g. labels stay in a field named `th`).
- After changing UI text run `python -m tools.i18n_check`: it must report 0 missing.
- The user guide (📖 tab) is `static/docs/guide.<code>.html`, one `<section id>` per topic. Update it
  when a workflow or a button name changes; it quotes the app's labels.

## Keep it simple for a first-time user

The 2D route has five steps: upload → character info + English description → reference views
(optional) → AI drawings → download. Anything the tool does on its own (building the sprite) is not
a step, and cards for features that don't exist yet stay hidden on the 2D route. The character
description can always be typed by hand; Claude (in chat or by API key) is optional.

## Releases

`core/version.py` holds the version (shown next to the name in the top bar and in `/api/version`). To
release: bump it, push to `main`, wait for CI to pass on every OS, then create the GitHub release
`v<VERSION>` with notes. Never rewrite `main`'s history: the repo is public.

## Running

- Server: `python -m uvicorn main:app --port 8000` from the project's virtualenv. Restart after
  Python changes; static JS/CSS changes need only a browser refresh.
- Blender (optional, 3D route) is found via `BLENDER_PATH` or the default install location.
- Built sprites live in `renders/<project>/<slug>/sprite/` (`build.json` is the build report).

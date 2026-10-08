# Instructions for AI coding agents (Codex and others)

Follow [CLAUDE.md](CLAUDE.md): it holds this project's rules and is not specific to one assistant.
Its AI tasks apply to you too:

- **Classify character `<slug>`** ("จำแนกตัวละคร `<slug>`", also "จำแนกโปรเจกต์"): fill in a character's profile with
  `python -m tools.ai_tasks list / show / apply`.
- **Review character `<slug>`** ("ตรวจงานตัวละคร `<slug>`", also "ตรวจงานโปรเจกต์"): look at the drawn rows with
  `python -m tools.ai_tasks review` and save verdicts with `apply-review`.

Reply in the language the user writes in. Never call a paid API or ask for an API key for these
tasks: you do them in the chat.

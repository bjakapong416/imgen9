# Security

ImGen9 runs on your own PC and listens on `127.0.0.1` only. It has no accounts. Your images and projects
stay on your PC, except when you turn on an online service yourself: with your own `ANTHROPIC_API_KEY`,
the character-info step sends the concept image to the Claude API. API keys are only sent to their own
service.

## Reporting a vulnerability

Please **don't** open a public issue for a security problem. Use GitHub's private report instead:
[Report a vulnerability](https://github.com/bjakapong416/imgen9/security/advisories/new) (Security tab →
Report a vulnerability). If that isn't possible, send a direct message to a maintainer on
[Discord](https://discord.gg/NwuzX3aZkP).

Include what you found, how to reproduce it, and what someone could do with it. You'll get an answer
within a week. Fixes go into the next release, and you're credited unless you'd rather not be.

## Supported versions

Only the latest release gets security fixes.

## Things to keep in mind

- Don't expose the server to a network (`--host 0.0.0.0`) unless you trust everyone on it: it is meant
  for one person on their own machine.
- Keep API keys in environment variables, never in a project file or a chat.

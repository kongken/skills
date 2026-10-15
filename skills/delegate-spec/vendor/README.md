# Vendored TDD

Source: [mattpocock/skills, `skills/engineering/tdd` at `49dd158`](https://github.com/mattpocock/skills/tree/49dd158/skills/engineering/tdd).
License: [MIT](LICENSE-mattpocock-skills).

`delegate-tdd/SKILL.md` is a condensed adaptation for unattended execution: seams come from the orchestrator, missing optional skills do not block work, and unresolved questions produce a blocked report. The red→green loop and public-boundary testing rules are retained. `tests.md` and `mocking.md` retain the pinned upstream examples.

Both agent adapters explicitly load this local copy. Installing or loading upstream `tdd` or `implement-spec` is not required at runtime; it would duplicate instructions and defeat isolation. The upstream preparation skills remain user-side dependencies documented in the repository README.

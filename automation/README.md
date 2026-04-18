# Automation

- `prompts/`: tracked prompt templates used by the local automation flow.
- `launchd/`: tracked macOS LaunchAgent templates.
- `inbox/`: generated review notes such as `gemini_review.md`.
- `logs/`: runtime log files.
- `state/`: runtime JSON status files.

`inbox/`, `logs/`, and `state/` are kept in the repo for visibility, but their generated contents are git-ignored so local runs do not dirty the working tree.

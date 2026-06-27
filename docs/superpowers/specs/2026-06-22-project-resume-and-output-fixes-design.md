# Project Resume and Output Fixes

**Date:** 2026-06-22
**Status:** approved

## Scope

- Preserve audio when concatenating generated clips.
- Respect `ffmpeg.path` from `config.yaml` for frame extraction and concatenation.
- Reject duplicate prompt IDs before creating or loading a chain.
- Create a new project only for a newly selected external `prompts.json`.
- Resume when the selected file is a project's `state.json` or its `input/prompts.json`.
- Reuse the project created during the current GUI session when Start is pressed
  again without selecting another prompt file.
- Keep aborted downgrade clips as `failed`, so resume regenerates them.
- Open project folders with the native Windows file manager.
- Project folder names use minute precision, with a collision suffix when needed.

## Deliberate Non-Changes

- Grok `/imagine` navigation remains hardcoded.
- Existing FlowRunner step-failure behavior remains unchanged. Its masking
  behavior will be documented, not modified.
- No full test-suite run; only focused tests and syntax checks are used.

## Resume Resolution

The GUI resolves the selected prompt path as follows:

1. `state.json` inside a project folder: load that project.
2. `input/prompts.json` with a sibling parent project containing `state.json`:
   load that project.
3. Any other JSON file: create a new project and copy the inputs.

The GUI remembers the selected prompt path and resolved project. Repeated Start
with the same selection resumes that project. Selecting a different external
prompt file creates a new project.

## Validation

Prompt payloads must be non-empty lists. Every item must contain an integer-like
`id` and a `prompt`. Duplicate normalized IDs are rejected before project state
is written.

## Verification

Run only focused tests for project setup/resume, chain runner behavior, frame
extraction configuration, and video concatenation.

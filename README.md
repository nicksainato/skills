# Nick's agent skills

Reusable agent skills maintained by [Nick Sainato](https://github.com/nicksainato).
Each skill is self-contained under `skills/` and can be installed independently.

## Available skills

### `whisperkit`

Private, on-device audio and video transcription for Apple Silicon using
WhisperKit. It includes a tested transcription wrapper that produces Markdown,
text, SRT, or JSON, plus an optional tap-or-hold dictation setup for macOS.

Media stays on the Mac. Model weights are downloaded from Argmax's official
Hugging Face repository on first use and are never stored in this Git
repository.

## Install

Clone the repository somewhere stable:

```bash
git clone https://github.com/nicksainato/skills.git
cd skills
```

Link the skill into the host you use:

```bash
# Codex
mkdir -p ~/.codex/skills
ln -s "$PWD/skills/whisperkit" ~/.codex/skills/whisperkit

# Claude Code
mkdir -p ~/.claude/skills
ln -s "$PWD/skills/whisperkit" ~/.claude/skills/whisperkit

# Oh My Pi
mkdir -p ~/.omp/agent/skills
ln -s "$PWD/skills/whisperkit" ~/.omp/agent/skills/whisperkit
```

Choose only the host links you need. If a destination already exists, inspect
it before replacing it.

## WhisperKit prerequisites

The skill targets Apple Silicon with macOS 13 or newer and Python 3.10+.
Install runtime dependencies only when you want to use it:

```bash
brew install whisperkit-cli ffmpeg
```

`ffmpeg` is needed for video input. See
[`skills/whisperkit/SKILL.md`](skills/whisperkit/SKILL.md) for usage and model
selection.

## Test

Tests use a stub transcription engine and do not download model weights:

```bash
python3 -m unittest discover -s tests -v
```

## Repository hygiene

Model weights, media, transcripts, caches, virtual environments, and secrets
are ignored. Do not force-add them. This repository should contain only skill
instructions, lightweight scripts, templates, and tests.

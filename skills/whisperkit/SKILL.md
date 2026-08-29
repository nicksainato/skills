---
name: whisperkit
description: >
  Transcribe a local audio or video file on-device with WhisperKit
  (whisperkit-cli) on Apple Silicon. Use when the user asks for speech to
  text from a recording, interview, course audio, voice memo, or other media,
  and when the work must stay offline on the machine.
---
# WhisperKit transcription

Speech to text for one local file. Audio never leaves the machine. The
models are CoreML weights that run on the Apple Neural Engine.

The skill verb is: transcribe a file. Hold-to-talk dictation is an optional
local setup, off by default. See [references/dictation.md](references/dictation.md).
Do not install it unless the user asks.

## Skill root

`$SKILL_ROOT` is the directory that contains this `SKILL.md`. Hosts do not
always export that variable. Resolve it as the directory of this file.

```bash
python3 "$SKILL_ROOT/scripts/transcribe.py" --help
```

The script locates itself with `Path(__file__)`. Do not hardcode a clone path.

## Command

```bash
python3 "$SKILL_ROOT/scripts/transcribe.py" \
  --input /path/to/interview.m4a \
  --out /path/to/transcript.md \
  --title "Client interview"
```

Dry-run (checks the input and the output path, reports whether
`whisperkit-cli` is installed, adds `ffmpeg` when the input is video, prints
the command, writes nothing):

```bash
python3 "$SKILL_ROOT/scripts/transcribe.py" \
  --input /path/to/interview.m4a \
  --dry-run
```

Options: `--format md|txt|srt|json` (default from the `--out` extension),
`--model`, `--model-path`, `--cache-dir`, `--language en`, `--task translate`,
`--word-timestamps`, `--keep-audio`, `--verbose`.

Video input (`.mp4`, `.mov`, `.mkv`, `.webm`, and similar) is converted to
16 kHz mono WAV with `ffmpeg` first. Audio input is passed through.

## Model

| Model | Disk | Use |
| --- | --- | --- |
| `openai_whisper-large-v3-v20240930_turbo` | 1.5 GB | Default. Speed plus accuracy. |
| `openai_whisper-large-v3-v20240930_626MB` | about 630 MB | Hard audio, accents, low volume. Slower per minute. |

`--model` accepts the Hugging Face folder name above or the bare variant
(`large-v3-v20240930_turbo`). `WHISPERKIT_MODEL` overrides the default.

The first run downloads one model from
`https://huggingface.co/argmaxinc/whisperkit-coreml` into
`~/Documents/huggingface/models/argmaxinc/whisperkit-coreml/<model>/`, plus a
small tokenizer under `~/Documents/huggingface/models/openai/`. `--cache-dir`
moves that base directory.

Later runs do not download the model again, but `--model` still contacts
Hugging Face to resolve the name. On a machine with no network, pass
`--model-path` at the cached model folder instead. That path is verified
offline.

Do not clone the whole model repository. It holds every Whisper variant.
One model folder is enough. See [references/models.md](references/models.md)
for the sparse prefetch and for `--model-path`.

## Dictation (optional)

A person who wants a local dictation key can install
[references/dictation.md](references/dictation.md). Tap F5 to start/stop or
hold it momentarily; the text is pasted into the focused field. A floating
pill shows whether the microphone is open and metering real audio.

```bash
"$SKILL_ROOT/scripts/whisperkit-dictate" status
```

The setup needs Karabiner-Elements, small listener and paste app bundles for
narrow privacy grants, and a login item. An agent must not install any of
that on its own.

## Rules

- Transcribe a local file. Download the source first if it is on Drive or a URL.
- Do not install Homebrew packages without asking the user first.
- Report the model used and the language that was detected.
- Do not invent speech. This wrapper produces no speaker labels. For those,
  `whisperkit-cli transcribe --diarization` exists; the wrapper does not use it.
- If no speech is found, the run fails and writes nothing.
- Long audio takes minutes. Use `--verbose` when progress must be visible.
- Fail explicitly if `whisperkit-cli` or `ffmpeg` is missing.
- Apple Silicon only. On Intel or Linux, say so and stop.

## When not to use

- Screen-aware transcripts of a Meet call, webinar, or screen share, where
  slides and shared UI matter. Use a screen-aware video transcription workflow.
- Media for a rendered video project. Use that project's media-ingestion workflow.
- Replacing Wispr Flow, Superwhisper, or MacWhisper for someone who already
  uses one. The optional dictation setup is for a person who asks for it.
- Cloning the full `argmaxinc/whisperkit-coreml` repository.
- Intel Macs, Linux, or CI runners without Apple Silicon.

## Prerequisites

- Apple Silicon Mac, macOS 13 or later
- Python 3.10+
- `whisperkit-cli` (`brew install whisperkit-cli`)
- `ffmpeg` for video input (`brew install ffmpeg`)
- 1.5 GB of disk for the default model, once per machine

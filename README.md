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

#### Dictation

Tap a key, speak, tap again; the text is transcribed locally and pasted into
whatever you were typing in. A floating pill shows whether the microphone is
actually open, metering the audio that will be transcribed rather than a
second microphone stream, so a missing permission shows as a flat line while
you are still talking instead of an empty paste afterwards. Drag the pill to
any screen edge; the side anchors stand it on end.

It is a hotkey, four small parts, and no subscription:

| Part | Job | Privacy grant |
| --- | --- | --- |
| `whisperkit-dictate` | Records, transcribes, copies, pastes | none |
| `WhisperKit Dictation.app` | Runs the listener that owns the microphone | Microphone |
| `WhisperKit Paste.app` | Sends one Command-V | Accessibility |
| `WhisperKit Indicator.app` | Draws the pill | none |

macOS assigns a microphone grant to whatever app starts a process chain, and
a command run by a hotkey tool has no grant: it records digital silence and
reports no error. The listener and the separate app bundles exist to put each
grant on the smallest possible thing, rather than on `/bin/bash`.

##### Quick start

Apple Silicon, macOS 13 or newer.

**1. Install what it runs on.**

```bash
brew install whisperkit-cli ffmpeg
xcode-select --install          # builds the helper apps below
```

**2. Link the command and build the apps.** Follow steps 1 to 4 of
[`dictation.md`](skills/whisperkit/references/dictation.md); each is one
copy-paste block. That gives you `whisperkit-dictate` on your `PATH`, plus
`WhisperKit Dictation.app` (the listener), `WhisperKit Paste.app`, and
`WhisperKit Indicator.app` in `~/Applications`.

**3. Approve exactly two permissions.** macOS prompts for each the first time
it is needed, in System Settings → Privacy & Security:

| Permission | Grant it to | Why |
| --- | --- | --- |
| **Microphone** | WhisperKit Dictation | The listener records. Without this macOS returns digital silence and no error. |
| **Accessibility** | WhisperKit Paste | Sends the one Command-V that pastes the transcript. |

Leave **Bash**, **Terminal**, and **Script Editor** unchecked. If macOS offers
you one of those instead, something is running the helper directly rather than
launching it: see the paste note in `dictation.md`. Nothing else here needs a
grant, and no audio leaves the machine.

**4. Pick a trigger.**

- *Keyboard:* copy the Karabiner rule (step 5 of `dictation.md`) and enable
  **F5: tap to toggle WhisperKit dictation, or hold to talk**.
- *Logitech mouse:* build `WhisperKit Toggle.app` and point a button at it
  with Logi Options+ → Buttons → Open Application. See
  [Mouse button setup](skills/whisperkit/references/dictation.md#mouse-button-setup-optional).
  Logi Options+ can only open an app, which is what that app is for.

**5. Start it at login** (step 7), so the listener is always there.

Then tap, say a sentence, and tap again. The pill appears at the bottom of the
screen: amber for the moment the microphone is opening, red with a live meter
while it hears you. `whisperkit-dictate status` reports every part if
something looks wrong.

See [`skills/whisperkit/references/dictation.md`](skills/whisperkit/references/dictation.md)
for the settings, the troubleshooting table, and what was measured.

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

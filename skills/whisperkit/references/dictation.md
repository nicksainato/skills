# Tap-or-hold dictation (optional)

This is an optional local setup, not the main skill. The skill verb is
"transcribe a file". This page adds a local dictation key on one Mac.

It does not replace Wispr Flow, Superwhisper, or MacWhisper. Those are paid
products with polish this does not have. Use this when someone wants
on-device dictation with no subscription and no audio leaving the machine,
or when they already run WhisperKit and want one more use for it.

An agent must not install this without being asked. Hotkeys, login items,
and privacy grants belong to the person who owns the laptop.

## What it does

Tap F5, speak, then tap again; or hold F5 while speaking and release. The
text lands on the clipboard and is pasted into the focused field. About one
second for a short sentence.

## Parts

| Part | Job |
| --- | --- |
| `scripts/whisperkit-dictate` | Records, transcribes, copies, pastes. |
| `whisperkit-cli serve` | Keeps the model in memory (tens of MB resident; it grows after the first request). Started on demand by the script. |
| A listener process | Owns the microphone. See "Why a listener" below. |
| `WhisperKit Dictation.app` | Runs the listener, holds the microphone grant, starts at login. |
| `WhisperKit Paste.app` | Sends Command-V without granting Accessibility to Bash or Script Editor. |
| Karabiner-Elements | Maps F5 or an alternative key to tap-to-toggle and hold-to-talk commands. |

## Why a listener, and why an app bundle

macOS gives a microphone grant to the app that starts a process chain. A
command run by a hotkey tool has no grant, and macOS returns digital silence
rather than an error. A recording that reads -91 dB peak is that failure.

So the recording happens in a listener process, and the listener is started
by a small app bundle. macOS asks about an app and remembers the answer. A
plain LaunchAgent does not work: launchd jobs get no prompt.

`whisperkit-dictate start` and `stop` detect the listener and pass the work
to it, so the hotkey command stays the same either way.

## Install

Everything below is run by the person who owns the machine.

1. Put the script on `PATH`:

```bash
mkdir -p ~/.local/bin
ln -sf "$SKILL_ROOT/scripts/whisperkit-dictate" ~/.local/bin/whisperkit-dictate
# ~/.local/bin is not on PATH by default on macOS
grep -q '.local/bin' ~/.zshrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
```

Open a new shell and check with `command -v whisperkit-dictate`.

2. Build the listener app bundle that owns the microphone grant:

```bash
APP="$HOME/Applications/WhisperKit Dictation.app"
mkdir -p "$APP/Contents/MacOS"
xcrun swiftc -O "$SKILL_ROOT/scripts/whisperkit-listen.swift" \
  -o "$APP/Contents/MacOS/whisperkit-listen"
/usr/libexec/PlistBuddy \
  -c "Add :CFBundleName string 'WhisperKit Dictation'" \
  -c "Add :CFBundleIdentifier string dev.nicksainato.whisperkit-dictation" \
  -c "Add :CFBundleExecutable string whisperkit-listen" \
  -c "Add :CFBundlePackageType string APPL" \
  -c "Add :CFBundleShortVersionString string 1.0" \
  -c "Add :CFBundleVersion string 1" \
  -c "Add :LSUIElement bool true" \
  -c "Add :NSMicrophoneUsageDescription string 'WhisperKit Dictation records your speech locally for transcription.'" \
  "$APP/Contents/Info.plist"
codesign -s - --force "$APP"
open "$APP"
```

Use the native launcher shown above. Launch Services can reject a shell
script used directly as `CFBundleExecutable` with `kLSNoExecutableErr`.

3. Build the narrowly scoped native paste helper. This prevents the shell
   listener from needing the broad Accessibility grant that macOS would
   otherwise assign to `/bin/bash`:

```bash
PASTE_APP="$HOME/Applications/WhisperKit Paste.app"
mkdir -p "$PASTE_APP/Contents/MacOS"
xcrun swiftc -O "$SKILL_ROOT/scripts/whisperkit-paste.swift" \
  -o "$PASTE_APP/Contents/MacOS/whisperkit-paste"
/usr/libexec/PlistBuddy \
  -c "Add :CFBundleName string 'WhisperKit Paste'" \
  -c "Add :CFBundleIdentifier string dev.nicksainato.whisperkit-paste" \
  -c "Add :CFBundleExecutable string whisperkit-paste" \
  -c "Add :CFBundlePackageType string APPL" \
  -c "Add :CFBundleShortVersionString string 1.0" \
  -c "Add :CFBundleVersion string 1" \
  -c "Add :LSUIElement bool true" \
  "$PASTE_APP/Contents/Info.plist"
codesign -s - --force "$PASTE_APP"
open -g -j -n "$PASTE_APP" --args --check
```

The last command opens the Accessibility prompt the first time. Enable only
**WhisperKit Paste** in System Settings → Privacy & Security → Accessibility.
Leave Bash and Script Editor disabled.

4. Install the hotkey. Copy the rule, then enable it in the app:

```bash
mkdir -p ~/.config/karabiner/assets/complex_modifications
cp "$SKILL_ROOT/templates/karabiner-dictation.json" \
   ~/.config/karabiner/assets/complex_modifications/
open -a Karabiner-Elements
```

Karabiner-Elements → Complex Modifications → Add rule → enable **F5: tap to
toggle WhisperKit dictation, or hold to talk**. Enable one rule only.

The F5 rule replaces the macOS system Dictation key. Tap once to keep
listening, tap again to stop/transcribe/paste, or hold F5 for a short
momentary recording. The template retains Caps Lock and Right Command as
hold-to-talk alternatives for keyboards where F5 is unavailable.

5. Start one recording. Approve Microphone access for **WhisperKit
   Dictation**, then finish the recording. Accessibility belongs only to
   **WhisperKit Paste**, from the previous step.

6. Start it at login:

```bash
osascript -e 'tell application "System Events" to make login item at end with properties {path:"'"$HOME"'/Applications/WhisperKit Dictation.app", hidden:true}'
```

AppleScript has no backslash continuation. Keep that command on one line.

Rebuilding or re-signing either app can reset that app's grant. macOS then
asks again.

## Commands

```bash
whisperkit-dictate status        # listener, server, microphone, recording state
whisperkit-dictate start         # begin recording
whisperkit-dictate stop          # transcribe, copy, paste
whisperkit-dictate toggle        # idle -> start; recording -> stop
whisperkit-dictate hold-start    # Karabiner: mark a hold and start
whisperkit-dictate release       # Karabiner: end a hold, or toggle a tap
whisperkit-dictate listen        # run the listener in this terminal
whisperkit-dictate quit          # stop the listener
whisperkit-dictate stop-server   # free the model
```

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `WHISPERKIT_DICTATE_PASTE` | `1` | `0` copies without pasting. |
| `WHISPERKIT_DICTATE_LANGUAGE` | `en` | Empty means auto-detect. |
| `WHISPERKIT_DICTATE_MIC` | `MacBook Pro Microphone` | Input device name. If the name does not match, the script falls back to any device named "macbook" or "built-in", then to the first device. |
| `WHISPERKIT_DICTATE_MODEL` | `large-v3-v20240930_turbo` | Model variant. |
| `WHISPERKIT_DICTATE_MAX_SECONDS` | `120` | Hard stop for one utterance. |
| `WHISPERKIT_DICTATE_MIN_PEAK_DB` | `-35` | Below this peak the audio counts as silence. |
| `WHISPERKIT_DICTATE_MIN_SECONDS` | `0.4` | Shorter recordings are discarded. |
| `WHISPERKIT_DICTATE_SOUNDS` | `1` | `0` turns off all success, failure, and optional start sounds. |
| `WHISPERKIT_DICTATE_START_SOUND` | `Purr` | Filename from `/System/Library/Sounds`, without `.aiff`. Set it empty for silent start. |
| `WHISPERKIT_DICTATE_SERVER` | `http://localhost:50060` | Change it if port 50060 is taken. |
| `WHISPERKIT_DICTATE_DIR` | `$TMPDIR/whisperkit-dictate` | Where the recording, log, and control pipe live. |
| `WHISPERKIT_DICTATE_FFMPEG` | found on `PATH` | Absolute path to `ffmpeg`. |
| `WHISPERKIT_DICTATE_ENGINE` | found on `PATH` | Absolute path to `whisperkit-cli`. |
| `WHISPERKIT_DICTATE_OPEN` | `/usr/bin/open` | Absolute path to the macOS app launcher. |
| `WHISPERKIT_DICTATE_PASTE_APP` | `~/Applications/WhisperKit Paste.app` | Path to the native paste app. It is launched separately so macOS attributes Accessibility to this app, not its shell parent. |

Sounds: Purr on start, Pop on success, Basso on silence or failure.

## The silence gate

Whisper invents text when it hears nothing. Three seconds of room tone
produced `Продолжение следует...` during development. The script now measures
the peak level first and discards anything quieter than -35 dB, without
touching the clipboard.

Measured on one M-series MacBook Pro: blocked microphone -91 dB, room tone
-39 dB, normal speech -26 dB or louder. Use the peak, not the average.
Leading and trailing silence drags the average below a speaking voice.

## Troubleshooting

Read the log. `whisperkit-dictate status` prints its path, which is
`$TMPDIR/whisperkit-dictate/dictate.log` by default. That directory is
per-user and mode 700, because recorded speech is private.

| Log line | Cause |
| --- | --- |
| `silence (peak -91.0 dB)` | The recorder has no microphone grant. The listener is not running, or the app was rebuilt. |
| `silence (peak -39 dB)` | Real room tone. Nothing was said, or the speaker is too far away. |
| `no audio captured` | A privacy prompt is waiting, or the microphone is held by another app. |
| `characters copied` and nothing pasted | Enable WhisperKit Paste—not Bash or Script Editor—in Privacy & Security → Accessibility. The text is on the clipboard. |
| `send ... failed: listener is not running` | Start the app: `open -a "WhisperKit Dictation"`. |
| `A dictation listener is already running` | One listener owns the pipe. Two would each read half of every command, so the second is refused. |

## Removal

```bash
whisperkit-dictate quit
whisperkit-dictate stop-server
rm ~/.config/karabiner/assets/complex_modifications/karabiner-dictation.json
rm -rf "$HOME/Applications/WhisperKit Dictation.app"
rm -rf "$HOME/Applications/WhisperKit Paste.app"
rm ~/.local/bin/whisperkit-dictate
```

Then remove the rule in Karabiner-Elements and the login item in System
Settings → General → Login Items.

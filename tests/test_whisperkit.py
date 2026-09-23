#!/usr/bin/env python3
"""Safe whisperkit checks. No model download and no whisperkit-cli run."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "whisperkit"
CLI = SKILL / "scripts" / "transcribe.py"

sys.path.insert(0, str(SKILL / "scripts"))
import transcribe  # noqa: E402

SAMPLE = {
    "text": " Hello there. Second line.",
    "language": "en",
    "timings": {"inputAudioSeconds": 3723.5},
    "segments": [
        {"id": 0, "start": 0.0, "end": 2.4, "text": " Hello there."},
        {"id": 1, "start": 2.4, "end": 5.0, "text": " Second line."},
        {"id": 2, "start": 5.0, "end": 5.1, "text": "   "},
    ],
}

# whisperkit-cli leaves special tokens in the report when the caller does not
# pass --skip-special-tokens. A real run produced this shape.
TOKENIZED = {
    "text": "<|startoftranscript|><|en|><|transcribe|><|0.00|> Hello there.<|2.48|>",
    "language": "en",
    "timings": {"inputAudioSeconds": 9.29},
    "segments": [
        {
            "id": 0,
            "start": 0.0,
            "end": 2.48,
            "text": "<|startoftranscript|><|en|><|transcribe|><|0.00|> Hello there.<|2.48|>",
        }
    ],
}


class ModelNameTests(unittest.TestCase):
    def test_default_model_is_the_turbo_variant(self) -> None:
        self.assertEqual(transcribe.DEFAULT_MODEL, "openai_whisper-large-v3-v20240930_turbo")

    def test_hugging_face_folder_name_is_split(self) -> None:
        self.assertEqual(
            transcribe.normalize_model("openai_whisper-large-v3-v20240930_turbo"),
            ("large-v3-v20240930_turbo", "openai"),
        )
        self.assertEqual(
            transcribe.normalize_model("openai_whisper-large-v3-v20240930_626MB"),
            ("large-v3-v20240930_626MB", "openai"),
        )
        self.assertEqual(
            transcribe.normalize_model("distil-whisper_distil-large-v3_turbo"),
            ("distil-large-v3_turbo", "distil"),
        )

    def test_bare_variant_passes_through(self) -> None:
        self.assertEqual(
            transcribe.normalize_model("large-v3-v20240930_turbo"),
            ("large-v3-v20240930_turbo", "openai"),
        )

    def test_empty_model_fails(self) -> None:
        with self.assertRaises(transcribe.TranscribeError):
            transcribe.normalize_model("  ")


class CommandTests(unittest.TestCase):
    def _command(self, **kwargs: object) -> list[str]:
        args = dict(
            audio=Path("/tmp/a.wav"),
            report_dir=Path("/tmp/report"),
            model=transcribe.DEFAULT_MODEL,
            model_path=None,
            cache_dir=None,
            language=None,
            task="transcribe",
            word_timestamps=False,
            verbose=False,
        )
        args.update(kwargs)
        return transcribe.build_command(**args)  # type: ignore[arg-type]

    def test_default_command_requests_a_report(self) -> None:
        cmd = self._command()
        self.assertEqual(cmd[:2], ["whisperkit-cli", "transcribe"])
        self.assertIn("--report", cmd)
        self.assertIn("--skip-special-tokens", cmd)
        self.assertIn("--audio-path", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "large-v3-v20240930_turbo")
        self.assertEqual(cmd[cmd.index("--model-prefix") + 1], "openai")
        self.assertNotIn("--language", cmd)
        self.assertNotIn("--task", cmd)

    def test_model_path_skips_download_flags(self) -> None:
        cmd = self._command(model_path="/models/openai_whisper-large-v3-v20240930_turbo")
        self.assertIn("--model-path", cmd)
        self.assertNotIn("--model", cmd)
        self.assertNotIn("--download-model-path", cmd)

    def test_options_are_passed_through(self) -> None:
        cmd = self._command(
            language="en",
            task="translate",
            word_timestamps=True,
            cache_dir="/cache",
            verbose=True,
        )
        self.assertEqual(cmd[cmd.index("--language") + 1], "en")
        self.assertEqual(cmd[cmd.index("--task") + 1], "translate")
        self.assertEqual(cmd[cmd.index("--download-model-path") + 1], "/cache")
        self.assertIn("--word-timestamps", cmd)
        self.assertIn("--verbose", cmd)


class InputTests(unittest.TestCase):
    def test_video_is_converted_and_audio_is_not(self) -> None:
        self.assertTrue(transcribe.needs_conversion(Path("a.mp4")))
        self.assertTrue(transcribe.needs_conversion(Path("a.MOV")))
        self.assertTrue(transcribe.needs_conversion(Path("a.webm")))
        self.assertFalse(transcribe.needs_conversion(Path("a.wav")))
        self.assertFalse(transcribe.needs_conversion(Path("a.m4a")))
        self.assertFalse(transcribe.needs_conversion(Path("a.MP3")))

    def test_ffmpeg_command_makes_16k_mono_wav(self) -> None:
        cmd = transcribe.ffmpeg_command(Path("in.mov"), Path("out.wav"))
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("-vn", cmd)
        self.assertEqual(cmd[cmd.index("-ar") + 1], "16000")
        self.assertEqual(cmd[cmd.index("-ac") + 1], "1")

    def test_format_follows_the_output_extension(self) -> None:
        self.assertEqual(transcribe.output_format(Path("a.srt"), None), "srt")
        self.assertEqual(transcribe.output_format(Path("a.json"), None), "json")
        self.assertEqual(transcribe.output_format(Path("a.txt"), None), "txt")
        self.assertEqual(transcribe.output_format(Path("a.rst"), None), "md")
        self.assertEqual(transcribe.output_format(Path("a.srt"), "md"), "md")
        self.assertEqual(transcribe.output_format(None, None), "md")


class RenderTests(unittest.TestCase):
    def test_timestamps(self) -> None:
        self.assertEqual(transcribe.format_timestamp(0), "00:00:00")
        self.assertEqual(transcribe.format_timestamp(3723.5), "01:02:03")
        self.assertEqual(transcribe.format_timestamp(3723.5, srt=True), "01:02:03,500")

    def test_rounding_never_makes_a_sixty_second_field(self) -> None:
        self.assertEqual(transcribe.format_timestamp(59.9996, srt=True), "00:01:00,000")
        self.assertEqual(transcribe.format_timestamp(3599.9999, srt=True), "01:00:00,000")
        self.assertEqual(transcribe.format_timestamp(59.9996), "00:00:59")

    def test_null_fields_do_not_crash(self) -> None:
        report = {"segments": [{"start": None, "end": None, "text": " Hi."}], "text": None}
        self.assertEqual(transcribe.clean_text(None), "")
        self.assertIn("[00:00:00] Hi.", transcribe.render_markdown(
            report, title="T", source=Path("a"), model="m"))
        self.assertNotIn("None", transcribe.render_text({"segments": [], "text": None}))

    def test_no_speech_is_detected(self) -> None:
        self.assertFalse(transcribe.has_speech({"segments": [], "text": ""}))
        self.assertFalse(transcribe.has_speech({"segments": [], "text": None}))
        self.assertTrue(transcribe.has_speech({"segments": [], "text": "hello"}))
        self.assertTrue(transcribe.has_speech(SAMPLE))

    def test_srt_falls_back_to_plain_text(self) -> None:
        srt = transcribe.render_srt(
            {"segments": [], "text": " hello ", "timings": {"inputAudioSeconds": 2.0}}
        )
        self.assertIn("00:00:00,000 --> 00:00:02,000", srt)
        self.assertIn("hello", srt)

    def test_markdown_has_header_and_one_line_per_segment(self) -> None:
        text = transcribe.render_markdown(
            SAMPLE, title="Client call", source=Path("/tmp/call.m4a"), model=transcribe.DEFAULT_MODEL
        )
        self.assertIn("# Client call", text)
        self.assertIn("- Source: /tmp/call.m4a", text)
        self.assertIn(transcribe.DEFAULT_MODEL, text)
        self.assertIn("- Language: en", text)
        self.assertIn("- Audio length: 01:02:03", text)
        self.assertIn("[00:00:00] Hello there.", text)
        self.assertIn("[00:00:02] Second line.", text)
        self.assertNotIn("[00:00:05]", text)

    def test_text_and_srt(self) -> None:
        self.assertEqual(transcribe.render_text(SAMPLE), "Hello there.\nSecond line.\n")
        srt = transcribe.render_srt(SAMPLE)
        self.assertIn("1\n00:00:00,000 --> 00:00:02,400\nHello there.", srt)
        self.assertIn("2\n00:00:02,400 --> 00:00:05,000\nSecond line.", srt)

    def test_special_tokens_are_stripped(self) -> None:
        self.assertEqual(transcribe.clean_text(TOKENIZED["text"]), "Hello there.")
        text = transcribe.render_markdown(
            TOKENIZED, title="T", source=Path("/tmp/a.aiff"), model="m"
        )
        self.assertIn("[00:00:00] Hello there.", text)
        self.assertNotIn("<|", text)
        self.assertNotIn("<|", transcribe.render_srt(TOKENIZED))
        self.assertEqual(transcribe.render_text(TOKENIZED), "Hello there.\n")

    def test_json_round_trips(self) -> None:
        data = json.loads(transcribe.render(SAMPLE, "json", title="t", source=Path("a"), model="m"))
        self.assertEqual(data["language"], "en")

    def test_missing_report_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="whisperkit-report-") as tmp:
            with self.assertRaises(transcribe.TranscribeError):
                transcribe.read_report(Path(tmp), Path("/tmp/clip.wav"))


class CliTests(unittest.TestCase):
    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *argv],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_help_lists_the_main_options(self) -> None:
        result = self._run(["--help"])
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--input", "--out", "--model", "--model-path", "--dry-run"):
            self.assertIn(flag, result.stdout)

    def test_missing_input_fails(self) -> None:
        result = self._run(
            ["--input", "/tmp/does-not-exist-whisperkit.m4a", "--out", "/tmp/out.md", "--dry-run"]
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("Input not found", result.stderr)

    def test_out_is_required_without_dry_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="whisperkit-") as tmp:
            audio = Path(tmp) / "clip.wav"
            audio.write_bytes(b"RIFF0000WAVE")
            result = self._run(["--input", str(audio)])
            self.assertEqual(result.returncode, 1)
            self.assertIn("--out is required", result.stderr)

    def test_dry_run_writes_nothing_and_prints_the_command(self) -> None:
        with tempfile.TemporaryDirectory(prefix="whisperkit-") as tmp:
            audio = Path(tmp) / "clip.wav"
            audio.write_bytes(b"RIFF0000WAVE")
            out = Path(tmp) / "transcript.md"
            result = self._run(["--input", str(audio), "--out", str(out), "--dry-run"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("whisperkit-cli transcribe", result.stdout)
            self.assertIn("large-v3-v20240930_turbo", result.stdout)
            self.assertIn("extract audio: no", result.stdout)
            self.assertIn("whisperkit-cli:", result.stdout)
            self.assertFalse(out.exists())

    def test_dry_run_reports_a_video_conversion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="whisperkit-") as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
            result = self._run(["--input", str(video), "--dry-run"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("extract audio: yes (ffmpeg)", result.stdout)
            self.assertIn("clip.wav", result.stdout)

    def test_unwritable_output_fails_before_transcribing(self) -> None:
        """A finished transcript must never be lost to a bad --out path."""
        with tempfile.TemporaryDirectory(prefix="whisperkit-") as tmp:
            audio = Path(tmp) / "clip.wav"
            audio.write_bytes(b"RIFF0000WAVE")
            locked = Path(tmp) / "locked"
            locked.mkdir()
            locked.chmod(0o500)
            try:
                result = self._run(["--input", str(audio), "--out", str(locked / "out.md")])
                self.assertEqual(result.returncode, 1)
                self.assertIn("permission to write", result.stderr)
            finally:
                locked.chmod(0o700)

    def test_out_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="whisperkit-") as tmp:
            audio = Path(tmp) / "clip.wav"
            audio.write_bytes(b"RIFF0000WAVE")
            result = self._run(["--input", str(audio), "--out", tmp])
            self.assertEqual(result.returncode, 1)
            self.assertIn("is a directory", result.stderr)

    def test_dry_run_creates_no_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="whisperkit-") as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"\x00\x00\x00\x18ftypmp42")
            keep = Path(tmp) / "deep" / "nested" / "audio.wav"
            result = self._run(
                ["--input", str(video), "--dry-run", "--keep-audio", str(keep)]
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(keep.parent.exists(), "dry run created directories")

    def test_bad_model_path_fails(self) -> None:
        with tempfile.TemporaryDirectory(prefix="whisperkit-") as tmp:
            audio = Path(tmp) / "clip.wav"
            audio.write_bytes(b"RIFF0000WAVE")
            result = self._run(
                [
                    "--input",
                    str(audio),
                    "--model-path",
                    str(Path(tmp) / "nope"),
                    "--dry-run",
                ]
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("--model-path is not a directory", result.stderr)


class StubEngineTests(unittest.TestCase):
    """Full path with a stub whisperkit-cli. No model and no real ASR."""

    STUB = """#!/usr/bin/env python3
import json, sys
from pathlib import Path

args = sys.argv[1:]
audio = Path(args[args.index("--audio-path") + 1])
report = Path(args[args.index("--report-path") + 1])
report.mkdir(parents=True, exist_ok=True)
(report / (audio.stem + ".json")).write_text(json.dumps(%s), encoding="utf-8")
print("Stub transcription")
""" % json.dumps(SAMPLE)

    def _stub_env(self, tmp: Path) -> dict[str, str]:
        import os

        stub = tmp / "bin" / "whisperkit-cli"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub.write_text(self.STUB, encoding="utf-8")
        stub.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{stub.parent}:{env.get('PATH', '')}"
        return env

    def test_audio_input_writes_markdown(self) -> None:
        if sys.platform == "win32":
            self.skipTest("stub uses a POSIX shebang")
        with tempfile.TemporaryDirectory(prefix="whisperkit-stub-") as tmp:
            work = Path(tmp)
            audio = work / "call.wav"
            audio.write_bytes(b"RIFF0000WAVE")
            out = work / "call.md"
            result = subprocess.run(
                [sys.executable, str(CLI), "--input", str(audio), "--out", str(out), "--title", "Call"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=self._stub_env(work),
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            text = out.read_text(encoding="utf-8")
            self.assertIn("# Call", text)
            self.assertIn("[00:00:00] Hello there.", text)
            self.assertIn("segments: 3", result.stdout)

    def test_model_path_is_named_in_the_header(self) -> None:
        if sys.platform == "win32":
            self.skipTest("stub uses a POSIX shebang")
        with tempfile.TemporaryDirectory(prefix="whisperkit-stub-") as tmp:
            work = Path(tmp)
            audio = work / "call.wav"
            audio.write_bytes(b"RIFF0000WAVE")
            models = work / "models" / "openai_whisper-tiny"
            models.mkdir(parents=True)
            out = work / "call.md"
            result = subprocess.run(
                [
                    sys.executable, str(CLI),
                    "--input", str(audio),
                    "--out", str(out),
                    "--model-path", str(models),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=self._stub_env(work),
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"- Model: {models}", out.read_text(encoding="utf-8"))

    def test_report_without_speech_writes_nothing(self) -> None:
        if sys.platform == "win32":
            self.skipTest("stub uses a POSIX shebang")
        empty_stub = self.STUB.replace(json.dumps(SAMPLE), json.dumps(
            {"text": "", "language": "en", "timings": {"inputAudioSeconds": 2.0}, "segments": []}
        ))
        with tempfile.TemporaryDirectory(prefix="whisperkit-stub-") as tmp:
            work = Path(tmp)
            stub = work / "bin" / "whisperkit-cli"
            stub.parent.mkdir(parents=True)
            stub.write_text(empty_stub, encoding="utf-8")
            stub.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{stub.parent}:{env.get('PATH', '')}"
            audio = work / "quiet.wav"
            audio.write_bytes(b"RIFF0000WAVE")
            out = work / "quiet.md"
            result = subprocess.run(
                [sys.executable, str(CLI), "--input", str(audio), "--out", str(out)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("no speech", result.stderr)
            self.assertFalse(out.exists(), "an empty transcript file was written")

    def test_video_input_is_converted_then_transcribed(self) -> None:
        import shutil as _shutil

        if sys.platform == "win32":
            self.skipTest("stub uses a POSIX shebang")
        if not _shutil.which("ffmpeg"):
            self.skipTest("ffmpeg")
        with tempfile.TemporaryDirectory(prefix="whisperkit-stub-") as tmp:
            work = Path(tmp)
            video = work / "clip.mp4"
            make = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=160x120:d=1",
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-shortest", "-c:v", "libx264", "-c:a", "aac", str(video),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if make.returncode != 0:
                self.skipTest(f"could not generate test video: {make.stderr[-200:]}")
            out = work / "clip.srt"
            result = subprocess.run(
                [sys.executable, str(CLI), "--input", str(video), "--out", str(out)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=self._stub_env(work),
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("00:00:00,000 --> 00:00:02,400", out.read_text(encoding="utf-8"))


class DictationTests(unittest.TestCase):
    """The optional tap-or-hold setup. No microphone and no hotkey needed."""

    DICTATE = SKILL / "scripts" / "whisperkit-dictate"
    LISTENER = SKILL / "scripts" / "whisperkit-listen.swift"
    PASTE = SKILL / "scripts" / "whisperkit-paste.swift"
    RULE = SKILL / "templates" / "karabiner-dictation.json"

    def test_script_is_executable_and_parses(self) -> None:
        self.assertTrue(self.DICTATE.is_file())
        self.assertTrue(os.access(self.DICTATE, os.X_OK), "whisperkit-dictate is not executable")
        result = subprocess.run(
            ["bash", "-n", str(self.DICTATE)], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_usage_without_arguments(self) -> None:
        result = subprocess.run(
            [str(self.DICTATE)], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage:", result.stderr)
        for verb in ("listen", "start", "stop", "status"):
            self.assertIn(verb, result.stderr)

    def test_documented_settings_exist_in_the_script(self) -> None:
        """Every variable in the reference table must be read by the script."""
        script = self.DICTATE.read_text(encoding="utf-8")
        doc = (SKILL / "references" / "dictation.md").read_text(encoding="utf-8")
        documented = set(re.findall(r"`(WHISPERKIT_DICTATE_[A-Z_]+)`", doc))
        self.assertGreaterEqual(len(documented), 8, "the settings table lost rows")
        for name in documented:
            self.assertIn(name, script, f"{name} is documented but unused")
        for name in set(re.findall(r"\$\{(WHISPERKIT_DICTATE_[A-Z_]+)", script)):
            if name in {"WHISPERKIT_DICTATE_CURL", "WHISPERKIT_DICTATE_PBCOPY",
                        "WHISPERKIT_DICTATE_OSASCRIPT", "WHISPERKIT_DICTATE_AFPLAY",
                        "WHISPERKIT_DICTATE_PYTHON", "WHISPERKIT_DICTATE_SYSTEM_MIC"}:
                continue  # test seams, not user settings
            self.assertIn(name, doc, f"{name} is a setting but is not documented")
        # The peak level is the gate, not the average. Leading silence drags
        # the average below a speaking voice.
        self.assertIn("max_volume", script)
        self.assertNotIn("MIN_MEAN_DB", script)

    def test_binaries_are_resolved_not_hardcoded(self) -> None:
        text = self.DICTATE.read_text(encoding="utf-8")
        self.assertIn("find_bin", text)
        self.assertNotIn('FFMPEG="${WHISPERKIT_DICTATE_FFMPEG:-/opt/homebrew', text)

    def test_paste_uses_a_narrow_native_helper(self) -> None:
        script = self.DICTATE.read_text(encoding="utf-8")
        helper = self.PASTE.read_text(encoding="utf-8")
        self.assertIn("WHISPERKIT_DICTATE_PASTE_APP", script)
        self.assertIn('"$OPEN" -g -j -n "$PASTE_APP"', script)
        self.assertNotIn('tell application "System Events" to keystroke', script)
        self.assertIn("AXIsProcessTrustedWithOptions", helper)
        self.assertIn("cghidEventTap", helper)

    def test_listener_uses_a_native_app_entry_point(self) -> None:
        launcher = self.LISTENER.read_text(encoding="utf-8")
        instructions = (SKILL / "references" / "dictation.md").read_text(encoding="utf-8")
        self.assertIn("Process()", launcher)
        self.assertIn("whisperkit-dictate", launcher)
        self.assertIn('["listen"]', launcher)
        self.assertIn("NSMicrophoneUsageDescription", instructions)

    def test_karabiner_template_is_portable(self) -> None:
        data = json.loads(self.RULE.read_text(encoding="utf-8"))
        commands = json.dumps(data)
        self.assertIn("$HOME/.local/bin/whisperkit-dictate", commands)
        self.assertNotIn("/Users/", commands)
        self.assertEqual(len(data["rules"]), 3)
        for rule in data["rules"]:
            manipulator = rule["manipulators"][0]
            self.assertIn("to_if_held_down", manipulator)
            self.assertIn("to_after_key_up", manipulator)
        recommended = data["rules"][0]
        self.assertIn("F5", recommended["description"])
        recommended_commands = json.dumps(recommended)
        self.assertIn("whisperkit-dictate hold-start", recommended_commands)
        self.assertIn("whisperkit-dictate release", recommended_commands)

    def test_tap_and_hold_commands_share_the_toggle_state(self) -> None:
        script = self.DICTATE.read_text(encoding="utf-8")
        self.assertIn("cmd_hold_start", script)
        self.assertIn("cmd_release", script)
        self.assertIn("cmd_toggle", script)
        self.assertIn('HOLD_MARKER="$STATE_DIR/key-held"', script)
        self.assertNotIn("sound Tink", script)
        self.assertIn('WHISPERKIT_DICTATE_START_SOUND-Purr', script)

    # Stubs stand in for ffmpeg, whisperkit-cli, and pbcopy, so this runs the
    # real start/stop code with no microphone, no model, and no clipboard.
    FAKE_FFMPEG = """#!/bin/bash
case "$*" in
  *list_devices*)
    echo "[AVFoundation indev @ 0x1] AVFoundation audio devices:" >&2
    echo "[AVFoundation indev @ 0x1] [0] Fake Device" >&2
    echo "[AVFoundation indev @ 0x1] [2] MacBook Pro Microphone" >&2
    exit 1 ;;
  *volumedetect*)
    echo "  Duration: 00:00:02.00, bitrate: 256 kb/s" >&2
    echo "[Parsed_volumedetect_0 @ 0x1] mean_volume: -20.0 dB" >&2
    echo "[Parsed_volumedetect_0 @ 0x1] max_volume: -6.0 dB" >&2
    exit 0 ;;
esac
for arg in "$@"; do target="$arg"; done
printf 'RIFFfake' > "$target"
sleep 30
"""

    FAKE_ENGINE = """#!/bin/bash
echo "stub transcript from the fallback path"
"""

    def _stub_env(self, work: Path) -> dict[str, str]:
        binaries = work / "bin"
        binaries.mkdir(parents=True, exist_ok=True)
        for name, body in (("ffmpeg", self.FAKE_FFMPEG), ("whisperkit-cli", self.FAKE_ENGINE)):
            path = binaries / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        clipboard = work / "clipboard.txt"
        pbcopy = binaries / "pbcopy"
        pbcopy.write_text(f"#!/bin/bash\ncat > {clipboard}\n", encoding="utf-8")
        pbcopy.chmod(0o755)
        quiet = binaries / "quiet"
        quiet.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        quiet.chmod(0o755)
        env = os.environ.copy()
        env.update(
            WHISPERKIT_DICTATE_DIR=str(work / "state"),
            WHISPERKIT_DICTATE_FFMPEG=str(binaries / "ffmpeg"),
            WHISPERKIT_DICTATE_ENGINE=str(binaries / "whisperkit-cli"),
            WHISPERKIT_DICTATE_PBCOPY=str(pbcopy),
            WHISPERKIT_DICTATE_OSASCRIPT=str(quiet),
            WHISPERKIT_DICTATE_AFPLAY=str(quiet),
            # Port 1 is closed, so the run takes the offline fallback path.
            WHISPERKIT_DICTATE_SERVER="http://localhost:1",
            WHISPERKIT_DICTATE_PASTE="0",
            WHISPERKIT_DICTATE_INDICATOR="0",
            # The real default is the system input. Pin the stub microphone so
            # these tests do not follow whichever device this machine uses.
            WHISPERKIT_DICTATE_SYSTEM_MIC="MacBook Pro Microphone",
        )
        env.pop("WHISPERKIT_DICTATE_MIC", None)
        return env

    def _dictate(self, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.DICTATE), *args], capture_output=True, text=True, env=env, check=False
        )

    def test_start_and_stop_produce_text(self) -> None:
        if sys.platform == "win32":
            self.skipTest("bash stubs")
        with tempfile.TemporaryDirectory(prefix="whisperkit-dictate-") as tmp:
            work = Path(tmp)
            env = self._stub_env(work)
            self.assertEqual(self._dictate(env, "start").returncode, 0)
            result = self._dictate(env, "stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "stub transcript from the fallback path",
                (work / "clipboard.txt").read_text(encoding="utf-8"),
            )

    def _devices(self, env: dict[str, str], *labels: str) -> None:
        lines = [
            "#!/bin/bash",
            'case "$*" in',
            "  *list_devices*)",
            '    echo "[AVFoundation indev @ 0x1] AVFoundation audio devices:" >&2',
        ]
        for index, label in enumerate(labels):
            lines.append(f'    echo "[AVFoundation indev @ 0x1] [{index}] {label}" >&2')
        lines += [
            "    exit 1 ;;",
            "  *volumedetect*)",
            '    echo "  Duration: 00:00:02.00, bitrate: 256 kb/s" >&2',
            '    echo "[Parsed_volumedetect_0 @ 0x1] max_volume: -6.0 dB" >&2',
            "    exit 0 ;;",
            "esac",
            'for arg in "$@"; do target="$arg"; done',
            "printf 'RIFFfake' > \"$target\"",
            "sleep 30",
            "",
        ]
        path = Path(env["WHISPERKIT_DICTATE_FFMPEG"])
        path.write_text("\n".join(lines), encoding="utf-8")
        path.chmod(0o755)

    def test_system_input_is_used_instead_of_the_first_device(self) -> None:
        if sys.platform == "win32":
            self.skipTest("bash stubs")
        with tempfile.TemporaryDirectory(prefix="whisperkit-dictate-sysmic-") as tmp:
            work = Path(tmp)
            env = self._stub_env(work)
            env["WHISPERKIT_DICTATE_SYSTEM_MIC"] = "Elgato Wave:3"
            self._devices(env, "Jump Desktop Audio", "Elgato Wave:3")
            self.assertEqual(self._dictate(env, "start").returncode, 0)
            log = (work / "state" / "dictate.log").read_text(encoding="utf-8")
            self.assertIn("Elgato Wave:3 (index 1)", log)
            self.assertNotIn("index 0", log)
            status = self._dictate(env, "status")
            self.assertIn("mic:       Elgato Wave:3 (index 1)", status.stdout)
            self._dictate(env, "stop")

    def test_explicit_mic_overrides_the_system_input(self) -> None:
        if sys.platform == "win32":
            self.skipTest("bash stubs")
        with tempfile.TemporaryDirectory(prefix="whisperkit-dictate-pinmic-") as tmp:
            work = Path(tmp)
            env = self._stub_env(work)
            env["WHISPERKIT_DICTATE_SYSTEM_MIC"] = "Elgato Wave:3"
            env["WHISPERKIT_DICTATE_MIC"] = "Anker PowerConf C200"
            self._devices(env, "Jump Desktop Audio", "Elgato Wave:3", "Anker PowerConf C200")
            self.assertEqual(self._dictate(env, "start").returncode, 0)
            log = (work / "state" / "dictate.log").read_text(encoding="utf-8")
            self.assertIn("Anker PowerConf C200 (index 2)", log)
            self._dictate(env, "stop")

    def test_missing_system_input_does_not_open_the_first_device(self) -> None:
        if sys.platform == "win32":
            self.skipTest("bash stubs")
        with tempfile.TemporaryDirectory(prefix="whisperkit-dictate-nomic-") as tmp:
            work = Path(tmp)
            env = self._stub_env(work)
            env["WHISPERKIT_DICTATE_SYSTEM_MIC"] = "Elgato Wave:3"
            self._devices(env, "Jump Desktop Audio")
            result = self._dictate(env, "start")
            self.assertNotEqual(result.returncode, 0)
            log = (work / "state" / "dictate.log").read_text(encoding="utf-8")
            self.assertIn("no matching input", log)
            self.assertNotIn("start: mic", log)
            self.assertFalse((work / "state" / "ffmpeg.pid").exists())

    def test_release_toggles_while_hold_release_is_momentary(self) -> None:
        if sys.platform == "win32":
            self.skipTest("bash stubs")
        with tempfile.TemporaryDirectory(prefix="whisperkit-dictate-input-") as tmp:
            work = Path(tmp)
            env = self._stub_env(work)
            state = work / "state"

            # A tap release starts, and the next tap release stops.
            self.assertEqual(self._dictate(env, "release").returncode, 0)
            self.assertTrue((state / "ffmpeg.pid").exists())
            self.assertEqual(self._dictate(env, "release").returncode, 0)
            self.assertFalse((state / "ffmpeg.pid").exists())

            # Crossing the hold threshold leaves a marker so release stops
            # rather than toggling a new latched recording.
            self.assertEqual(self._dictate(env, "hold-start").returncode, 0)
            self.assertTrue((state / "key-held").exists())
            self.assertEqual(self._dictate(env, "release").returncode, 0)
            self.assertFalse((state / "key-held").exists())
            self.assertFalse((state / "ffmpeg.pid").exists())

    def test_empty_language_does_not_abort_the_shell(self) -> None:
        """bash 3.2 aborts on "${empty[@]}" under set -u. Auto-detect must work."""
        if sys.platform == "win32":
            self.skipTest("bash stubs")
        with tempfile.TemporaryDirectory(prefix="whisperkit-dictate-lang-") as tmp:
            work = Path(tmp)
            env = self._stub_env(work)
            env["WHISPERKIT_DICTATE_LANGUAGE"] = ""
            self._dictate(env, "start")
            result = self._dictate(env, "stop")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("unbound variable", result.stderr)
            self.assertIn(
                "stub transcript", (work / "clipboard.txt").read_text(encoding="utf-8")
            )

    def test_stale_pid_file_does_not_kill_another_process(self) -> None:
        if sys.platform == "win32":
            self.skipTest("bash stubs")
        with tempfile.TemporaryDirectory(prefix="whisperkit-dictate-pid-") as tmp:
            work = Path(tmp)
            env = self._stub_env(work)
            state = work / "state"
            state.mkdir(parents=True, exist_ok=True)
            victim = subprocess.Popen(["sleep", "30"])
            try:
                (state / "ffmpeg.pid").write_text(str(victim.pid), encoding="utf-8")
                self._dictate(env, "stop")
                self.assertIsNone(victim.poll(), "stop killed an unrelated process")
                self.assertFalse((state / "ffmpeg.pid").exists(), "stale pid file kept")
            finally:
                victim.kill()
                victim.wait()

    def test_state_directory_is_private(self) -> None:
        if sys.platform == "win32":
            self.skipTest("bash stubs")
        with tempfile.TemporaryDirectory(prefix="whisperkit-dictate-mode-") as tmp:
            work = Path(tmp)
            env = self._stub_env(work)
            self._dictate(env, "status")
            mode = (work / "state").stat().st_mode & 0o777
            self.assertEqual(mode, 0o700, f"recordings would be readable: {oct(mode)}")

    def test_dictation_reference_states_the_boundary(self) -> None:
        text = (SKILL / "references" / "dictation.md").read_text(encoding="utf-8")
        self.assertIn("Wispr Flow", text)
        self.assertIn("must not install", text)


class SkillFileTests(unittest.TestCase):
    def test_skill_is_independent_and_named(self) -> None:
        text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: whisperkit", text)
        self.assertIn("$SKILL_ROOT", text)
        metadata = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("WhisperKit Transcription", metadata)
        self.assertIn("$whisperkit", metadata)

    def test_no_personal_paths(self) -> None:
        for path in sorted(SKILL.rglob("*")):
            if not path.is_file() or path.suffix == ".pyc":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("/Users/", text, f"personal path in {path}")


if __name__ == "__main__":
    unittest.main()

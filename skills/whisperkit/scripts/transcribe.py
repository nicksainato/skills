#!/usr/bin/env python3
"""Transcribe a local audio or video file on-device with whisperkit-cli.

Thin wrapper. It converts video to 16 kHz mono WAV when needed, calls
`whisperkit-cli transcribe`, and renders the JSON report as markdown, text,
SRT, or JSON. It never installs software and never downloads models by
itself; whisperkit-cli downloads one model on first use.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_MODEL = "openai_whisper-large-v3-v20240930_turbo"
MODEL_ENV = "WHISPERKIT_MODEL"
ENGINE = "whisperkit-cli"

# Model folders in argmaxinc/whisperkit-coreml carry a publisher prefix.
# whisperkit-cli rebuilds it from --model-prefix, so the prefix is stripped
# here and passed separately.
MODEL_PREFIXES = {
    "openai_whisper-": "openai",
    "distil-whisper_": "distil",
}

# Audio formats whisperkit-cli reads directly. Everything else, video
# containers included, is converted with ffmpeg first.
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".aiff", ".aif"}

SPECIAL_TOKEN_RE = re.compile(r"<\|[^|>]*\|>")

FORMATS = ("md", "txt", "srt", "json")
FORMAT_BY_SUFFIX = {".md": "md", ".markdown": "md", ".txt": "txt", ".srt": "srt", ".json": "json"}


class TranscribeError(Exception):
    """User-facing failure."""


def normalize_model(name: str) -> tuple[str, str]:
    """Return (model argument, model prefix) for whisperkit-cli.

    Accepts a Hugging Face folder name (`openai_whisper-large-v3-v20240930_turbo`)
    or the bare variant (`large-v3-v20240930_turbo`).
    """
    value = name.strip()
    if not value:
        raise TranscribeError("--model must not be empty.")
    for prefix, cli_prefix in MODEL_PREFIXES.items():
        if value.startswith(prefix):
            return value[len(prefix):], cli_prefix
    return value, "openai"


def needs_conversion(path: Path) -> bool:
    """Anything that is not a known audio container goes through ffmpeg."""
    return path.suffix.lower() not in AUDIO_SUFFIXES


def has_speech(result: dict) -> bool:
    return bool(segment_lines(result) or clean_text(result.get("text")))


def output_format(out: Path | None, requested: str | None) -> str:
    if requested:
        return requested
    if out is not None:
        return FORMAT_BY_SUFFIX.get(out.suffix.lower(), "md")
    return "md"


def format_timestamp(seconds: float, *, srt: bool = False) -> str:
    # Round before splitting. Rounding after would turn 59.9996 into ":60".
    total = max(0.0, float(seconds))
    if srt:
        millis = int(round(total * 1000))
        hours, rest = divmod(millis, 3_600_000)
        minutes, rest = divmod(rest, 60_000)
        secs, millis = divmod(rest, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    whole = int(total)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_command(
    *,
    audio: Path,
    report_dir: Path,
    model: str | None,
    model_path: str | None,
    cache_dir: str | None,
    language: str | None,
    task: str,
    word_timestamps: bool,
    verbose: bool,
) -> list[str]:
    # --skip-special-tokens keeps <|startoftranscript|> and timestamp tokens
    # out of the segment text that the report carries.
    cmd = [
        ENGINE,
        "transcribe",
        "--audio-path",
        str(audio),
        "--skip-special-tokens",
        "--report",
        "--report-path",
        str(report_dir),
    ]
    if model_path:
        cmd += ["--model-path", model_path]
    else:
        variant, prefix = normalize_model(model or DEFAULT_MODEL)
        cmd += ["--model", variant, "--model-prefix", prefix]
        if cache_dir:
            cmd += ["--download-model-path", cache_dir]
    if language:
        cmd += ["--language", language]
    if task != "transcribe":
        cmd += ["--task", task]
    if word_timestamps:
        cmd.append("--word-timestamps")
    if verbose:
        cmd.append("--verbose")
    return cmd


def ffmpeg_command(source: Path, target: Path) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(target),
    ]


def convert_to_wav(source: Path, target: Path) -> None:
    result = subprocess.run(ffmpeg_command(source, target), capture_output=True, text=True, check=False)
    if result.returncode != 0 or not target.is_file():
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-10:]
        raise TranscribeError("ffmpeg could not extract audio:\n" + "\n".join(tail))


def clean_text(value: object) -> str:
    """Drop Whisper special tokens such as <|startoftranscript|> or <|3.26|>."""
    if value is None:
        return ""
    return " ".join(SPECIAL_TOKEN_RE.sub(" ", str(value)).split())


def as_seconds(value: object) -> float:
    """A report field may be null. Treat anything unusable as 0."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def segment_lines(result: dict) -> list[tuple[float, str]]:
    lines = []
    for segment in result.get("segments") or []:
        text = clean_text(segment.get("text", ""))
        if not text:
            continue
        lines.append((as_seconds(segment.get("start")), text))
    return lines


def audio_seconds(result: dict) -> float:
    timings = result.get("timings") or {}
    value = timings.get("inputAudioSeconds")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    ends = [as_seconds(s.get("end")) for s in result.get("segments") or []]
    return max(ends) if ends else 0.0


def render_markdown(result: dict, *, title: str, source: Path, model: str) -> str:
    lines = [
        f"# {title}",
        "",
        f"- Source: {source}",
        "- Engine: whisperkit-cli (on-device, Apple Silicon)",
        f"- Model: {model}",
        f"- Language: {result.get('language') or 'unknown'}",
        f"- Audio length: {format_timestamp(audio_seconds(result))}",
        "",
    ]
    body = segment_lines(result)
    if body:
        lines += [f"[{format_timestamp(start)}] {text}" for start, text in body]
    else:
        lines.append(clean_text(result.get("text", "")))
    return "\n".join(lines).rstrip() + "\n"


def render_text(result: dict) -> str:
    body = segment_lines(result)
    if body:
        return "\n".join(text for _, text in body) + "\n"
    return clean_text(result.get("text", "")) + "\n"


def render_srt(result: dict) -> str:
    blocks = []
    if not (result.get("segments") or []):
        # No segments, but the report may still carry plain text.
        body = clean_text(result.get("text"))
        if body:
            end = format_timestamp(audio_seconds(result) or 1.0, srt=True)
            return f"1\n{format_timestamp(0.0, srt=True)} --> {end}\n{body}\n"
    for index, segment in enumerate(result.get("segments") or [], start=1):
        text = clean_text(segment.get("text", ""))
        if not text:
            continue
        start = format_timestamp(as_seconds(segment.get("start")), srt=True)
        end = format_timestamp(as_seconds(segment.get("end")), srt=True)
        blocks.append(f"{index}\n{start} --> {end}\n{text}\n")
    return "\n".join(blocks)


def render(result: dict, fmt: str, *, title: str, source: Path, model: str) -> str:
    if fmt == "md":
        return render_markdown(result, title=title, source=source, model=model)
    if fmt == "txt":
        return render_text(result)
    if fmt == "srt":
        return render_srt(result)
    if fmt == "json":
        return json.dumps(result, indent=2) + "\n"
    raise TranscribeError(f"Unknown format {fmt!r}. Choose: {', '.join(FORMATS)}")


def check_writable(out: Path) -> None:
    """Fail before transcription if the output cannot be written."""
    if out.is_dir():
        raise TranscribeError(f"--out is a directory: {out}")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TranscribeError(f"Cannot create {out.parent}: {exc}") from exc
    if not os.access(out.parent, os.W_OK):
        raise TranscribeError(f"No permission to write in {out.parent}")
    if out.exists() and not os.access(out, os.W_OK):
        raise TranscribeError(f"No permission to overwrite {out}")


def read_report(report_dir: Path, audio: Path) -> dict:
    report = report_dir / f"{audio.stem}.json"
    if not report.is_file():
        raise TranscribeError(f"whisperkit-cli wrote no JSON report at {report}")
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TranscribeError(f"Could not read {report}: {exc}") from exc
    if not isinstance(data, dict):
        raise TranscribeError(f"Unexpected report format in {report}")
    return data


def run_engine(cmd: list[str], *, verbose: bool) -> None:
    if verbose:
        result = subprocess.run(cmd, stdout=sys.stderr, check=False)
        if result.returncode != 0:
            raise TranscribeError(f"{ENGINE} failed with exit code {result.returncode}")
        return
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-15:]
        raise TranscribeError(
            f"{ENGINE} failed with exit code {result.returncode}:\n" + "\n".join(tail)
        )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, help="Local audio or video file.")
    parser.add_argument("--out", help="Output file. Required unless --dry-run.")
    parser.add_argument(
        "--format",
        choices=FORMATS,
        help="Output format. Default: from the --out extension, else md.",
    )
    parser.add_argument(
        "--model",
        help=f"Model variant. Default: {DEFAULT_MODEL} (or ${MODEL_ENV}).",
    )
    parser.add_argument("--model-path", help="Local model folder. Skips any download.")
    parser.add_argument(
        "--cache-dir",
        help="Download base for models. Default: ~/Documents/huggingface.",
    )
    parser.add_argument("--language", help="Language code such as en. Default: auto-detect.")
    parser.add_argument(
        "--task",
        choices=("transcribe", "translate"),
        default="transcribe",
        help="translate produces English text from other languages.",
    )
    parser.add_argument("--title", help="Title for the markdown header.")
    parser.add_argument("--word-timestamps", action="store_true", help="Add word-level timings.")
    parser.add_argument(
        "--keep-audio",
        help="Write the extracted WAV to this path and keep it. Video input only.",
    )
    parser.add_argument("--verbose", action="store_true", help="Stream whisperkit-cli output.")
    parser.add_argument("--dry-run", action="store_true", help="Check inputs and print the plan.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    source = Path(args.input).expanduser()
    out = Path(args.out).expanduser() if args.out else None
    fmt = output_format(out, args.format)
    model = args.model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
    title = args.title or source.stem

    try:
        if not source.is_file():
            raise TranscribeError(f"Input not found: {source}")
        if out is None and not args.dry_run:
            raise TranscribeError("--out is required unless --dry-run is set.")
        if args.model_path:
            folder = Path(args.model_path).expanduser()
            if not folder.is_dir():
                raise TranscribeError(f"--model-path is not a directory: {folder}")
        if out is not None and not args.dry_run:
            # Check now. Failing after an hour of transcription would throw
            # the result away.
            check_writable(out)

        convert = needs_conversion(source)
        engine_path = shutil.which(ENGINE)
        ffmpeg_path = shutil.which("ffmpeg")
        if not args.dry_run:
            if convert and not ffmpeg_path:
                raise TranscribeError(
                    f"ffmpeg is required to extract audio from {source.suffix or 'this file'}. "
                    "Install it with: brew install ffmpeg"
                )
            if not engine_path:
                raise TranscribeError(
                    f"{ENGINE} is not on PATH. Install it with: brew install whisperkit-cli "
                    "(Apple Silicon, macOS 13+). Ask the user before installing anything."
                )

        with tempfile.TemporaryDirectory(prefix="whisperkit-") as tmp:
            work = Path(tmp)
            audio = source
            if convert:
                audio = Path(args.keep_audio).expanduser() if args.keep_audio else work / f"{source.stem}.wav"
                if not args.dry_run:
                    audio.parent.mkdir(parents=True, exist_ok=True)
            report_dir = work / "report"
            report_dir.mkdir(parents=True, exist_ok=True)

            cmd = build_command(
                audio=audio,
                report_dir=report_dir,
                model=model,
                model_path=args.model_path,
                cache_dir=args.cache_dir,
                language=args.language,
                task=args.task,
                word_timestamps=args.word_timestamps,
                verbose=args.verbose,
            )

            if args.dry_run:
                engine_state = engine_path or "not installed (brew install whisperkit-cli)"
                ffmpeg_state = ffmpeg_path or "not installed (brew install ffmpeg)"
                print(f"Dry run: {source}")
                print(f"  extract audio: {'yes (ffmpeg)' if convert else 'no'}")
                print(f"  model:         {args.model_path or model}")
                print(f"  format:        {fmt}")
                print(f"  out:           {out if out else '(none)'}")
                print(f"  whisperkit-cli: {engine_state}")
                if convert:
                    print(f"  ffmpeg:        {ffmpeg_state}")
                print("  command:       " + " ".join(cmd))
                print("No transcription ran and no file was written.")
                if not engine_path or (convert and not ffmpeg_path):
                    print("Install the missing tool before a real run. Ask the user first.")
                return 0

            if convert:
                convert_to_wav(source, audio)
            print(
                f"Transcribing {source.name} with {args.model_path or model}. "
                "A model that is not in the cache downloads first.",
                file=sys.stderr,
            )
            run_engine(cmd, verbose=args.verbose)
            result = read_report(report_dir, audio)

        if out is None:
            raise TranscribeError("--out is required unless --dry-run is set.")
        if not has_speech(result):
            raise TranscribeError(
                f"{ENGINE} found no speech in {source.name}. Nothing was written. "
                "Check the audio, or try --language to name the spoken language."
            )
        try:
            out.write_text(
                render(result, fmt, title=title, source=source, model=args.model_path or model),
                encoding="utf-8",
            )
        except OSError as exc:
            raise TranscribeError(f"Could not write {out}: {exc}") from exc
    except TranscribeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {out}")
    print(f"  language: {result.get('language') or 'unknown'}")
    print(f"  audio:    {format_timestamp(audio_seconds(result))}")
    print(f"  segments: {len(result.get('segments') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running this file directly from within the project's app/ package
# without needing to `pip install` the project itself.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.subtitles import resolve_subtitle_segments  # noqa: E402
from app.services.transcriber import transcribe  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402


def seconds_to_srt_timestamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def segments_to_srt(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(
            f"{seconds_to_srt_timestamp(seg['start'])} --> {seconds_to_srt_timestamp(seg['end'])}"
        )
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)


def get_subtitles(video_path: Path, whisper_model_size: str = "base") -> tuple[list[dict], str]:
    """Returns (segments, source) where source is 'embedded', 'sibling', or
    'whisper'. This is the whole feature in one function call.
    """
    scratch_dir = video_path.parent / ".subtitle_scratch"
    segments, source = resolve_subtitle_segments(video_path, scratch_dir)

    if source == "whisper":
        print(f"No subtitle found for '{video_path.name}' — transcribing with Whisper "
              f"(model='{whisper_model_size}')... this may take a while.", file=sys.stderr)
        model = WhisperModel(whisper_model_size, device="cpu", compute_type="int8")
        segments = transcribe(model, video_path, cleanup=False)

    return segments, source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path, help="Path to a local video file")
    parser.add_argument("--srt", type=Path, default=None, help="Write result as an .srt file to this path")
    parser.add_argument("--json", type=Path, default=None, help="Write result as JSON to this path")
    parser.add_argument("--whisper-size", default="base", help="faster-whisper model size if transcription is needed")
    args = parser.parse_args()

    if not args.video.exists():
        print(f"Error: file not found: {args.video}", file=sys.stderr)
        sys.exit(1)

    segments, source = get_subtitles(args.video, args.whisper_size)

    print(f"Source: {source}")
    print(f"Segments: {len(segments)}")
    for seg in segments[:5]:
        print(f"  [{seg['start']:.1f}s - {seg['end']:.1f}s] {seg['text'][:80]}")
    if len(segments) > 5:
        print(f"  ... and {len(segments) - 5} more")

    if args.srt:
        args.srt.write_text(segments_to_srt(segments), encoding="utf-8")
        print(f"Wrote {args.srt}")
    if args.json:
        args.json.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
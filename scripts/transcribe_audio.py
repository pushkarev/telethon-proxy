#!/usr/bin/env python3
import argparse
from pathlib import Path
from faster_whisper import WhisperModel


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio with faster-whisper")
    parser.add_argument("input", help="Path to audio/video file")
    parser.add_argument("--model", default="small", help="Whisper model size/name")
    parser.add_argument("--language", default="ru", help="Language code or 'auto'")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda", "auto"])
    parser.add_argument("--compute-type", default="int8", help="ctranslate2 compute type")
    parser.add_argument("--beam-size", type=int, default=5)
    args = parser.parse_args()

    audio_path = Path(args.input)
    if not audio_path.exists():
        raise SystemExit(f"Input file not found: {audio_path}")

    language = None if args.language == "auto" else args.language
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        beam_size=args.beam_size,
        vad_filter=True,
    )

    print(f"[detected_language={info.language} probability={info.language_probability:.3f} duration={info.duration:.1f}s]")
    for segment in segments:
        print(segment.text.strip())


if __name__ == "__main__":
    main()

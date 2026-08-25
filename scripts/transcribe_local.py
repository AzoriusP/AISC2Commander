from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _transcribe(model, audio: Path, language: str = "zh") -> dict[str, str]:
    if not audio.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio}")
    prompt = (
        "StarCraft II English tactical commands: Marine, Marauder, SCV, Banshee, "
        "Barracks, Command Center, Supply Depot, control group, move, attack, train, "
        "build, research, A1, A2."
        if language == "en"
        else "星际争霸二中文战术指令：陆战队员、枪兵、劫掠者、农民、女妖、兵营、"
        "指挥中心、补给站、编组、移动、攻击、生产、建造、升级、A1、A2。"
    )
    kwargs = {
        "beam_size": 5,
        "vad_filter": True,
        "initial_prompt": prompt,
    }
    kwargs["language"] = language
    segments, info = model.transcribe(str(audio), **kwargs)
    text = "".join(segment.text for segment in segments).strip()
    return {"text": text, "language": info.language}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path, nargs="?")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--model", default="small")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--language", choices=("zh", "en"), default="zh")
    args = parser.parse_args()
    if not args.serve and args.audio is None:
        parser.error("audio is required unless --serve is used")

    from faster_whisper import WhisperModel

    model = WhisperModel(
        args.model,
        device="cpu",
        compute_type="int8",
        download_root=str(args.model_dir),
    )
    if not args.serve:
        assert args.audio is not None
        print(json.dumps(_transcribe(model, args.audio, args.language), ensure_ascii=False))
        return 0

    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                break
            audio = Path(str(request["audio"]))
            payload = _transcribe(model, audio, args.language)
        except Exception as error:
            payload = {"error": str(error)}
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

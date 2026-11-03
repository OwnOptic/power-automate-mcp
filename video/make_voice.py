"""Generate the voiceover for every scene in both languages, and the timeline Remotion reads.

    pip install edge-tts
    python make_voice.py            # all scenes, both languages
    python make_voice.py --lang fr  # one language

Writes public/audio/<lang>/<scene>.mp3 and src/timeline.json. The timeline holds each
scene's length in frames (voice plus a short pause) and the sentence boundaries the
voice engine reports, so the captions on screen change exactly when the voice does.
Re-run it after editing narration.json; the video follows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path

import edge_tts

HERE = Path(__file__).resolve().parent
FPS = 30
PAUSE_S = 0.9  # breathing room after each scene's last sentence
LEAD_S = 0.35  # silence before the voice starts, so a cut never clips the first word


async def synthesise(text: str, voice: str, out: Path) -> tuple[float, list[dict]]:
    """Write the mp3; return its length in seconds and the sentence timings.

    The service occasionally answers a burst of requests with no audio at all
    (NoAudioReceived); a short wait and a retry is all it needs.
    """
    for attempt in range(5):
        try:
            return await _synthesise_once(text, voice, out)
        except edge_tts.exceptions.NoAudioReceived:
            if attempt == 4:
                raise
            await asyncio.sleep(3 * (attempt + 1))
    raise AssertionError("unreachable")


async def _synthesise_once(text: str, voice: str, out: Path) -> tuple[float, list[dict]]:
    communicate = edge_tts.Communicate(text, voice, rate="-4%", boundary="SentenceBoundary")
    sentences: list[dict] = []
    end = 0.0
    with out.open("wb") as audio:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio.write(chunk["data"])
            elif chunk["type"] in ("SentenceBoundary", "WordBoundary"):
                start = chunk["offset"] / 1e7
                stop = start + chunk["duration"] / 1e7
                sentences.append({"start": round(start, 3), "end": round(stop, 3), "text": chunk["text"]})
                end = max(end, stop)
    return end, sentences


async def main(langs: list[str]) -> None:
    spec = json.loads((HERE / "narration.json").read_text(encoding="utf-8"))
    path = HERE / "src" / "timeline.json"
    timeline = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"fps": FPS}
    for lang in langs:
        out_dir = HERE / "public" / "audio" / lang
        out_dir.mkdir(parents=True, exist_ok=True)
        scenes = []
        for scene in spec["scenes"]:
            seconds, sentences = await synthesise(scene[lang], spec["voices"][lang], out_dir / f"{scene['id']}.mp3")
            frames = math.ceil((LEAD_S + seconds + PAUSE_S) * FPS)
            scenes.append({"id": scene["id"], "frames": frames, "lead": LEAD_S,
                           "captions": [{**s, "start": s["start"] + LEAD_S, "end": s["end"] + LEAD_S} for s in sentences]})
            print(f"{lang} {scene['id']:8} {seconds:5.1f}s  {len(sentences)} sentences")
        timeline[lang] = scenes
        total = sum(s["frames"] for s in scenes) / FPS
        print(f"{lang} total {total:.0f}s")
    timeline["fps"] = FPS
    path.write_text(json.dumps(timeline, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", choices=["en", "fr"], action="append")
    asyncio.run(main(parser.parse_args().lang or ["en", "fr"]))

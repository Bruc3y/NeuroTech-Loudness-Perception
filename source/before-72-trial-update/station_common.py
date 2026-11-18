"""Portable stimulus inventory and pilot schedule; no hardware access on import."""
from pathlib import Path
import json
import random
import hashlib

ROOT = Path(__file__).resolve().parent
CATEGORIES = ("pleasant", "neutral", "unpleasant")
IMAGE_CODES = {"pleasant": 1, "neutral": 2, "unpleasant": 3}
TONE_CODES = {(category, level): 4 + index * 2 + offset
              for index, category in enumerate(CATEGORIES)
              for offset, level in enumerate(("70", "90"))}


def inventory():
    images = []
    for category in CATEGORIES:
        for path in sorted((ROOT / "web/assets/images" / category).glob("*.jpg")):
            images.append({"image_id": category + "/" + path.stem,
                           "valence": category, "filename": path.name,
                           "path": "/" + path.relative_to(ROOT / "web").as_posix(),
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    if any(sum(item["valence"] == c for item in images) != 24 for c in CATEGORIES):
        raise ValueError("Expected 24 images in each of the three categories")
    return {"version": "supplied-images-1s-v1", "images": images,
            "audio": {level: "/assets/audio/tone_target_" + level + "dbSPL_1s.wav"
                      for level in ("70", "90")},
            "tone_seconds": 1.0, "acoustic_calibration_verified": False}


def make_schedule(count=6, seed=None):
    if count not in (6, 72):
        raise ValueError("Choose six engineering trials or 72 pilot trials")
    rng = random.Random(seed)
    images = inventory()["images"]
    rows = []
    for category in CATEGORIES:
        pool = [image for image in images if image["valence"] == category]
        rng.shuffle(pool)
        per_level = count // 6
        for offset, level in enumerate(("70", "90")):
            for image in pool[offset * per_level:(offset + 1) * per_level]:
                rows.append({**image, "level_label": level,
                             "audio_file": "web/assets/audio/tone_target_" + level + "dbSPL_1s.wav",
                             "image_code": IMAGE_CODES[category], "tone_code": TONE_CODES[category, level]})
    rng.shuffle(rows)
    return [{"trial": index, **row} for index, row in enumerate(rows, 1)]


if __name__ == "__main__":
    (ROOT / "web/assets/manifest.json").write_text(json.dumps(inventory(), indent=2))
    print("Indexed 72 supplied images and two one-second WAV files")

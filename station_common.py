"""Portable stimulus inventory and pilot schedule; no hardware access on import."""
from pathlib import Path
import json
import random
import hashlib
from functools import lru_cache

ROOT = Path(__file__).resolve().parent
CATEGORIES = ("pleasant", "neutral", "unpleasant")
IMAGE_CODES = {"pleasant": 1, "neutral": 2, "unpleasant": 3}
TONE_CODES = {(category, level): 4 + index * 2 + offset
              for index, category in enumerate(CATEGORIES)
              for offset, level in enumerate(("70", "90"))}
# Image-onset-to-tone-onset interval (SOA), drawn uniformly per trial so the tone
# cannot be anticipated, and so picture-evoked activity is not time-locked to it.
SOA_RANGE_S = (1.0, 2.0)
# Fixation after the rating response, before the fixed 1 s pre-image fixation.
ITI_RANGE_S = (0.5, 1.5)


def trial_timing(count, seed):
    """Per-trial jittered timings, reproducible from the schedule seed.

    Uses its own random stream, so adding timing leaves the image/level order
    for a given seed unchanged. Values are rounded to the millisecond.
    """
    rng = random.Random() if seed is None else random.Random(f"{seed}-timing")
    return [{"soa_s": round(rng.uniform(*SOA_RANGE_S), 3),
             "iti_s": round(rng.uniform(*ITI_RANGE_S), 3)} for _ in range(count)]


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


def balanced_levels(per_level, rng):
    """Sample a balanced binary order with no run longer than two.

    Weight each next choice by its valid completions, avoiding both dead ends
    and a predictable forced alternation. Each valid level order is equiprobable.
    """
    @lru_cache(None)
    def completions(low, high, last, run):
        if low == high == 0:
            return 1
        total = 0
        for level, remaining in (("70", low), ("90", high)):
            if remaining and not (last == level and run == 2):
                total += completions(low - (level == "70"), high - (level == "90"),
                                     level, run + 1 if last == level else 1)
        return total

    low = high = per_level
    last, run = "", 0
    result = []
    while low or high:
        choices = []
        for level, remaining in (("70", low), ("90", high)):
            if remaining and not (last == level and run == 2):
                weight = completions(low - (level == "70"), high - (level == "90"),
                                     level, run + 1 if last == level else 1)
                if weight:
                    choices.append((level, weight))
        draw = rng.randrange(sum(weight for _, weight in choices))
        for level, weight in choices:
            if draw < weight:
                break
            draw -= weight
        result.append(level)
        low -= level == "70"
        high -= level == "90"
        run = run + 1 if level == last else 1
        last = level
    return result


def make_schedule(count=72, seed=None):
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
    pools = {level: [row for row in rows if row["level_label"] == level]
             for level in ("70", "90")}
    for pool in pools.values():
        rng.shuffle(pool)
    ordered = [pools[level].pop() for level in balanced_levels(count // 2, rng)]
    timing = trial_timing(count, seed)
    return [{"trial": index, **row, **timing[index - 1]} for index, row in enumerate(ordered, 1)]


def browser_schedule(seed):
    return [{"trial": row["trial"], "image_id": row["image_id"],
             "image_source": "supplied_finalized_image_set", "image_path": row["path"],
             "image_sha256": row["sha256"], "valence": row["valence"],
             "level": row["level_label"], "audio_path": inventory_audio(row["level_label"]),
             "gain": 0.1, "soa_s": row["soa_s"]} for row in make_schedule(72, seed)]


def inventory_audio(level):
    return "/assets/audio/tone_target_" + level + "dbSPL_1s.wav"


if __name__ == "__main__":
    (ROOT / "web/assets/manifest.json").write_text(json.dumps(inventory(), indent=2))
    print("Indexed 72 supplied images and two one-second WAV files")

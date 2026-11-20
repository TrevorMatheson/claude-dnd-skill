"""Milestone 0 trial for scene backgrounds (spec D12, AC1).

Makes about six scene images on the Gemini key so the user can judge them on
the TV before anything is built around image generation.

Every image request spends the user's free Gemini image quota, so this is a
DRY RUN unless --send is given: it prints the requests it would make and
how many, and makes none.

  python scene_trial.py                       # dry run: print the 6 requests
  python scene_trial.py --send                # make them (asks nothing more)
  python scene_trial.py --style "ink wash" --style "oil painting"   # compare styles
  python scene_trial.py --locations my_places.json                  # your own places
  python scene_trial.py list-models           # which image models the key sees (no quota)

--locations FILE is a JSON list of {"name": "...", "look": "..."}: the look is
what the party sees on arriving, the place and never the people in it.
Images go to <out>/<slug>.png (default <temp>/mydm-scene-trial) with an
index.json; ones already there are skipped unless --again. A 429 stops the
run at once.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import scene_gen  # noqa: E402
import scene_images  # noqa: E402
import tts  # noqa: E402

DEFAULT_MAX = 8
DEFAULT_STYLE = ("painterly fantasy illustration, muted colours, soft atmospheric "
                 "light, dark edges")

# Six places in the spirit of a real campaign (D12): a tavern, a cave, a forest
# road, a dungeon room, a town square and a ruin.
SAMPLE_LOCATIONS = [
    {"name": "The Rusty Flagon",
     "look": "a smoky tavern common room, low blackened beams, a fire in a stone hearth, "
             "empty tables and benches, tankards on the bar"},
    {"name": "Glimmer Cave",
     "look": "a wet limestone cavern, pale crystals in the walls catching torchlight, "
             "a still pool, stalactites dripping"},
    {"name": "The Old Mill Road",
     "look": "a muddy forest road under tall oaks at dusk, a leaning milestone, "
             "mist between the trunks"},
    {"name": "The Flooded Crypt",
     "look": "a half-submerged stone hall, carved pillars standing in black water, "
             "a stair descending into it, a faint green glow"},
    {"name": "Harrow Square",
     "look": "a cobbled town square at night with a dry fountain, shuttered market stalls "
             "and lanterns on the houses around it, rain on the stones"},
    {"name": "Sunken Abbey",
     "look": "a roofless ruined abbey, ivy over broken arches, fallen stones in long grass, "
             "a grey overcast sky"},
]


def _default_out() -> Path:
    return Path(tempfile.gettempdir()) / "mydm-scene-trial"


def _load_locations(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("the locations file must be a JSON list of {name, look}")
    locs = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) \
                or not item["name"].strip() or not isinstance(item.get("look", ""), str):
            raise ValueError(f"location {i + 1} needs a text name and look")
        locs.append({"name": item["name"].strip(), "look": item.get("look", "").strip()})
    return locs


def _jobs(locs: list, styles: list) -> list:
    """One job per location x style. With one style the file is the place's
    slug; with several, the style's slug is added so they sit side by side."""
    jobs = []
    for loc in locs:
        base = scene_images.slug(loc["name"]) or "place"
        for style in styles:
            name = base if len(styles) == 1 else f"{base}--{scene_images.slug(style) or 'style'}"
            jobs.append({"name": loc["name"], "look": loc["look"], "style": style, "slug": name})
    return jobs


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scene_trial.py", description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", nargs="?", choices=["list-models"],
                   help="list-models: show the image models the key can use (no quota spent)")
    p.add_argument("--locations", help="JSON list of {name, look} (default: six samples)")
    p.add_argument("--style", action="append", help="art style text; repeat to compare styles")
    p.add_argument("--out", help="output folder (default: <temp>/mydm-scene-trial)")
    p.add_argument("--model", default=scene_gen.GEMINI_IMAGE_MODEL)
    p.add_argument("--max", type=int, default=DEFAULT_MAX,
                   help=f"refuse to make more than this many requests (default {DEFAULT_MAX})")
    p.add_argument("--send", action="store_true", help="really make the requests (spends quota)")
    p.add_argument("--again", action="store_true", help="remake images that already exist")
    return p


def _list_models() -> int:
    if not tts._get_api_key():
        print("scene_trial: no Gemini key is set up", file=sys.stderr)
        return 2
    try:
        names = scene_gen.list_image_models()
    except scene_gen.SceneGenError as e:
        print(f"scene_trial: {e}", file=sys.stderr)
        return 1
    print("Image models this key can see (listing costs no quota):")
    for n in names:
        print(f"  {n}" + ("   <- default" if n == scene_gen.GEMINI_IMAGE_MODEL else ""))
    if not names:
        print("  (none)")
    return 0


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "list-models":
        return _list_models()

    try:
        locs = _load_locations(args.locations) if args.locations else list(SAMPLE_LOCATIONS)
    except (OSError, ValueError) as e:
        print(f"scene_trial: {e}", file=sys.stderr)
        return 2
    styles = args.style or [DEFAULT_STYLE]
    out = Path(args.out) if args.out else _default_out()

    jobs = _jobs(locs, styles)
    todo = [j for j in jobs if args.again or not (out / f"{j['slug']}.png").exists()]
    skipped = len(jobs) - len(todo)
    n = len(todo)

    if not args.send:
        for j in todo:
            body = scene_gen.build_request(j["look"], j["style"])
            print(f"- {j['slug']}.png  model={args.model}  "
                  f"aspect={body['generationConfig']['imageConfig']['aspectRatio']}  key=***")
            print(f"  prompt: {scene_gen.build_prompt(j['look'], j['style'])}")
        if skipped:
            print(f"({skipped} already in {out}; add --again to remake them)")
        print(f"Would make {n} image requests - none made (add --send)")
        if n > args.max:
            print(f"Note: that is over --max {args.max}; --send would refuse.")
        return 0

    if n > args.max:
        print(f"scene_trial: {n} image requests is over --max {args.max} - none made",
              file=sys.stderr)
        return 2
    if n and not tts._get_api_key():
        print("scene_trial: no Gemini key is set up (DND_TTS_KEY, GEMINI_API_KEY or "
              "~/.config/claude-dnd/tts.key) - none made", file=sys.stderr)
        return 2

    print(f"Making {n} image requests on your Gemini key (model {args.model})"
          + (f"; {skipped} already made" if skipped else ""), flush=True)
    out.mkdir(parents=True, exist_ok=True)
    index_path = out / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(index, dict):
            index = {}
    except (OSError, ValueError):
        index = {}

    made = 0
    code = 0
    for j in todo:
        try:
            img = scene_gen.generate(j["look"], j["style"], model=args.model)
        except scene_gen.SceneGenError as e:
            if e.status == 429:
                wait = f", retry in about {round(e.retry_after / 3600, 1)} h" if e.retry_after else ""
                print(f"scene_trial: quota used up ({e.quota_id or 'no quota id'}{wait}) - "
                      f"stopped after {made} made", file=sys.stderr)
                code = 3
                break
            print(f"scene_trial: {j['slug']}: {e}", file=sys.stderr)
            code = 1
            continue
        tmp = out / f".{j['slug']}.part"
        tmp.write_bytes(img)
        os.replace(tmp, out / f"{j['slug']}.png")
        index[j["slug"]] = {"name": j["name"], "look": j["look"], "style": j["style"],
                            "model": args.model, "file": f"{j['slug']}.png"}
        made += 1
        print(f"  made {j['slug']}.png ({len(img) // 1024} KB)", flush=True)

    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{made} of {n} made in {out}")
    return code


if __name__ == "__main__":
    sys.exit(main())

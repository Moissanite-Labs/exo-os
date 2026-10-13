#!/usr/bin/env python3
"""Accept only the complete synthetic GTK probe result expected by phase 3."""
import argparse
import json
import re
import sys

from phase3_input_evidence import native_repeat_delivered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("broadway-private", "wayland-layer"), required=True)
    parser.add_argument("--exit-code", type=int, required=True)
    args = parser.parse_args()
    if args.exit_code != 0:
        return 1
    try:
        actual = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeError):
        return 1
    expected = {"probe": "exo-surface-gtk-v1", "backend": args.backend,
                "layered": args.backend == "wayland-layer", "status": "PASS",
                "gsk_renderer": "cairo" if args.backend == "wayland-layer" else "default"}
    if not isinstance(actual, dict) or set(actual) != {*expected, "renderers", "held_f6"}:
        return 1
    held = actual.pop("held_f6")
    if args.backend == "wayland-layer":
        if (not isinstance(held, dict) or set(held) != {"mode", "events", "released", "a_allowed",
                                                     "b_responses", "reviewed", "prompt_text"}
                or held["mode"] != "native"
                or type(held["a_allowed"]) is not int or held["a_allowed"] != 1
                or type(held["b_responses"]) is not int or held["b_responses"] != 0
                or held["reviewed"] is not False or held["prompt_text"] != "yesterday"
                or not native_repeat_delivered(held["events"], released=held["released"])):
            return 1
    elif held != {"mode": "synthetic"}:
        return 1
    renderers = actual.pop("renderers")
    if not isinstance(renderers, dict) or set(renderers) != {"bar", "drawer", "card"}:
        return 1
    if args.backend == "wayland-layer":
        if any(value != "GskCairoRenderer" for value in renderers.values()):
            return 1
    elif not all(isinstance(value, str) and re.fullmatch(r"Gsk[A-Za-z0-9]+Renderer", value)
                 for value in renderers.values()):
        return 1
    return 0 if actual == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())

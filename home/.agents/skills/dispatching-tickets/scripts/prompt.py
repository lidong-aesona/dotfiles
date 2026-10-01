#!/usr/bin/env python3
"""Assemble a dispatch prompt from its parts, refusing if any part is missing or empty.

    python3 prompt.py <estate-dir> <n>                      # -> tickets/<n>/prompt.md
    python3 prompt.py <estate-dir> <n> --preface <file>     # -> tickets/<n>/<file stem>.prompt.md
    python3 prompt.py <estate-dir> <n> --review             # -> tickets/<n>/review.prompt.md
    add --profile <name> to any of these: the profile route.py chose (default: agents.active)

Order: [preface] + tickets/<n>/head.md + the estate's `prompt.blocks` + the profile's
`blocks`, all under blocks/. A preface (a handover) is followed by a line telling the new
agent that the original dispatch below it still applies. A review assembles
tickets/<n>/review-head.md + `prompt.review_blocks` + the profile's `blocks` instead: a
reviewer gets the review brief, never the mandate to close the ticket.

Blocks are read fresh on every assembly, so a handover carries today's estate facts rather
than the ones the original dispatch was written with.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import estate as E

SEAM = "\n\n---\n\nEverything below is the original dispatch. It still applies.\n\n"


def part(path):
    if not path.is_file() or not path.read_text().strip():
        sys.exit(f"!! {path} is missing or empty; refusing to assemble a prompt without it")
    text = path.read_text().strip()
    if "<!-- ESTATE" in text:
        sys.exit(f"!! {path} still holds an unanswered <!-- ESTATE --> placeholder")
    return text


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        sys.exit(__doc__)
    cfg = E.load(args[0])
    n = int(args[1].lstrip("#"))
    root, ticket = cfg["_root"], cfg["_root"] / "tickets" / str(n)
    name = args[args.index("--profile") + 1] if "--profile" in args else cfg["agents"]["active"]
    if name not in cfg["agents"]["profiles"]:
        sys.exit(f"!! no profile {name!r} in estate.toml")
    profile = cfg["agents"]["profiles"][name]
    if profile.get("exhausted_until"):
        # An agent started on an exhausted provider dies on its first turn, and the estate
        # contradicting itself means a migration was half-recorded.
        sys.exit(f"!! profile {name!r} is marked exhausted until {profile['exhausted_until']}; "
                 "route to another profile, or finish the migration in estate.toml first")
    review = "--review" in args
    if review and "--preface" in args:
        sys.exit("!! --review and --preface do not combine; a reviewer that stopped is re-reviewed from scratch")
    shared = cfg.get("prompt", {}).get("review_blocks" if review else "blocks", [])
    names = shared + profile.get("blocks", [])

    head = ticket / ("review-head.md" if review else "head.md")
    body = "\n\n".join([part(head)] + [part(root / "blocks" / b) for b in names])
    out = ticket / ("review.prompt.md" if review else "prompt.md")
    if "--preface" in args:
        preface = Path(args[args.index("--preface") + 1]).expanduser()
        if not preface.is_absolute():
            preface = root / preface
        body = part(preface) + SEAM + body
        out = ticket / f"{preface.stem}.prompt.md"
    out.write_text(body + "\n")
    print(out)


if __name__ == "__main__":
    main()

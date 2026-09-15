#!/usr/bin/env python3
"""sq.py — Ablage-Helfer fuer den /sidequest-Skill.

Verwaltet Seitenzweige einer Unterhaltung als JSON-Index plus je eine
Markdown-Datei pro Zweig. Keine externen Dependencies, Python 3.10+.

Storage-Layout (relativ zum Projekt-Root):
    .claude/sidequests/index.json
    .claude/sidequests/sq-001.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

STATUS_OPEN = "open"
STATUS_MERGED = "merged"
STATUS_DROPPED = "dropped"
STATUSES = (STATUS_OPEN, STATUS_MERGED, STATUS_DROPPED)

STATUS_GLYPH = {STATUS_OPEN: "○", STATUS_MERGED: "●", STATUS_DROPPED: "✕"}


def store_dir() -> Path:
    """Return the sidequest store, honouring SIDEQUEST_DIR for tests."""
    override = os.environ.get("SIDEQUEST_DIR")
    if override:
        return Path(override)
    return Path.cwd() / ".claude" / "sidequests"


def index_path() -> Path:
    return store_dir() / "index.json"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_index() -> dict:
    p = index_path()
    if not p.exists():
        return {"version": 1, "entries": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"index.json ist kaputt ({exc}). Bitte manuell pruefen: {p}")
    data.setdefault("version", 1)
    data.setdefault("entries", [])
    return data


def save_index(data: dict) -> None:
    store_dir().mkdir(parents=True, exist_ok=True)
    tmp = index_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(index_path())


def find(data: dict, sq_id: str) -> dict:
    sq_id = normalize_id(sq_id)
    for e in data["entries"]:
        if e["id"] == sq_id:
            return e
    sys.exit(f"Unbekannter Zweig: {sq_id}. `sq.py list --status all` zeigt alle.")


def normalize_id(raw: str) -> str:
    """Accept 3, '3', 'sq3', 'sq-3' and return the canonical 'sq-003'."""
    raw = raw.strip().lower()
    m = re.fullmatch(r"(?:sq-?)?(\d+)", raw)
    if not m:
        return raw
    return f"sq-{int(m.group(1)):03d}"


def next_id(data: dict) -> str:
    nums = []
    for e in data["entries"]:
        m = re.fullmatch(r"sq-(\d+)", e["id"])
        if m:
            nums.append(int(m.group(1)))
    return f"sq-{(max(nums) + 1) if nums else 1:03d}"


def body_path(sq_id: str) -> Path:
    return store_dir() / f"{sq_id}.md"


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_new(args: argparse.Namespace) -> None:
    data = load_index()
    parent = normalize_id(args.parent) if args.parent and args.parent != "main" else "main"
    if parent != "main":
        find(data, parent)  # validiert
    sq_id = next_id(data)
    entry = {
        "id": sq_id,
        "title": args.title,
        "question": args.question,
        "parent": parent,
        "status": STATUS_OPEN,
        "created": now(),
        "updated": now(),
        "merged_note": None,
        "tags": args.tag or [],
    }
    data["entries"].append(entry)
    save_index(data)

    body = body_path(sq_id)
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_text(
        f"# {sq_id} — {args.title}\n\n"
        f"- **Abzweig von:** {parent}\n"
        f"- **Erstellt:** {entry['created']}\n\n"
        f"## Frage\n\n{args.question}\n\n"
        f"## Antwort\n\n_(noch offen)_\n",
        encoding="utf-8",
    )
    print(sq_id)
    print(body)


def cmd_answer(args: argparse.Namespace) -> None:
    data = load_index()
    entry = find(data, args.id)
    text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    body = body_path(entry["id"])
    current = body.read_text(encoding="utf-8") if body.exists() else ""
    marker = "## Antwort\n"
    if marker in current:
        head, _ = current.split(marker, 1)
        current = head + marker
    else:
        current = current.rstrip() + "\n\n" + marker
    body.write_text(current + "\n" + text.strip() + "\n", encoding="utf-8")
    entry["updated"] = now()
    save_index(data)
    print(f"{entry['id']}: Antwort gespeichert -> {body}")


def _set_status(args: argparse.Namespace, status: str) -> None:
    data = load_index()
    entry = find(data, args.id)
    entry["status"] = status
    entry["updated"] = now()
    if args.note:
        entry["merged_note"] = args.note
    save_index(data)

    body = body_path(entry["id"])
    if body.exists():
        label = {STATUS_MERGED: "Rueckfuehrung", STATUS_DROPPED: "Verworfen"}[status]
        chunk = f"\n---\n\n## {label} ({entry['updated']})\n\n{args.note or '_(ohne Notiz)_'}\n"
        body.write_text(body.read_text(encoding="utf-8").rstrip() + "\n" + chunk, encoding="utf-8")
    print(f"{entry['id']}: {status}")


def cmd_merge(args: argparse.Namespace) -> None:
    _set_status(args, STATUS_MERGED)


def cmd_drop(args: argparse.Namespace) -> None:
    _set_status(args, STATUS_DROPPED)


def cmd_reopen(args: argparse.Namespace) -> None:
    data = load_index()
    entry = find(data, args.id)
    entry["status"] = STATUS_OPEN
    entry["updated"] = now()
    save_index(data)
    print(f"{entry['id']}: {STATUS_OPEN}")


def cmd_list(args: argparse.Namespace) -> None:
    data = load_index()
    entries = data["entries"]
    if args.status != "all":
        entries = [e for e in entries if e["status"] == args.status]
    if args.json:
        print(json.dumps(entries, indent=2, ensure_ascii=False))
        return
    if not entries:
        print("Keine Zweige.")
        return
    for e in entries:
        glyph = STATUS_GLYPH.get(e["status"], "?")
        parent = "" if e["parent"] == "main" else f"  (aus {e['parent']})"
        tags = f"  [{', '.join(e['tags'])}]" if e.get("tags") else ""
        print(f"{glyph} {e['id']}  {e['title']}{parent}{tags}")


def cmd_show(args: argparse.Namespace) -> None:
    data = load_index()
    entry = find(data, args.id)
    body = body_path(entry["id"])
    glyph = STATUS_GLYPH.get(entry["status"], "?")
    print(f"{glyph} {entry['id']} — Status: {entry['status']} (zuletzt {entry['updated']})\n")
    if body.exists():
        print(body.read_text(encoding="utf-8"))
    else:
        print(json.dumps(entry, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------
# map
# --------------------------------------------------------------------------

def _mermaid(entries: list[dict]) -> str:
    lines = [
        "flowchart LR",
        '  main(["Hauptverlauf"])',
    ]
    for e in entries:
        label = e["title"].replace('"', "'")
        lines.append(f'  {e["id"].replace("-", "_")}["{e["id"]}<br/>{label}"]')
    for e in entries:
        node = e["id"].replace("-", "_")
        parent = "main" if e["parent"] == "main" else e["parent"].replace("-", "_")
        lines.append(f"  {parent} --> {node}")
        if e["status"] == STATUS_MERGED:
            lines.append(f"  {node} -.rueckgefuehrt.-> main")
    lines += [
        "  classDef open fill:#00C8E8,stroke:#1D3C6E,color:#0b1b30;",
        "  classDef merged fill:#1A8BC4,stroke:#1D3C6E,color:#ffffff;",
        "  classDef dropped fill:#cfd6e0,stroke:#7a8699,color:#3b4657;",
    ]
    for status in STATUSES:
        ids = [e["id"].replace("-", "_") for e in entries if e["status"] == status]
        if ids:
            lines.append(f"  class {','.join(ids)} {status};")
    return "\n".join(lines)


def cmd_map(args: argparse.Namespace) -> None:
    data = load_index()
    entries = data["entries"]
    if not args.all:
        entries = [e for e in entries if e["status"] != STATUS_DROPPED]
    out = _mermaid(entries)
    if args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
        print(args.out)
    else:
        print(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sq.py", description="Seitenzweige einer Unterhaltung verwalten")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="neuen Zweig anlegen")
    n.add_argument("--title", required=True)
    n.add_argument("--question", required=True)
    n.add_argument("--parent", default="main", help="'main' oder eine sq-ID")
    n.add_argument("--tag", action="append")
    n.set_defaults(func=cmd_new)

    a = sub.add_parser("answer", help="Antwort an einen Zweig haengen")
    a.add_argument("id")
    a.add_argument("--file", help="Datei mit dem Antworttext; sonst stdin")
    a.set_defaults(func=cmd_answer)

    m = sub.add_parser("merge", help="Zweig als rueckgefuehrt markieren")
    m.add_argument("id")
    m.add_argument("--note")
    m.set_defaults(func=cmd_merge)

    d = sub.add_parser("drop", help="Zweig verwerfen")
    d.add_argument("id")
    d.add_argument("--note")
    d.set_defaults(func=cmd_drop)

    r = sub.add_parser("reopen", help="Zweig wieder oeffnen")
    r.add_argument("id")
    r.set_defaults(func=cmd_reopen)

    l = sub.add_parser("list", help="Zweige auflisten")
    l.add_argument("--status", choices=[*STATUSES, "all"], default="all")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="einen Zweig komplett ausgeben")
    s.add_argument("id")
    s.set_defaults(func=cmd_show)

    mp = sub.add_parser("map", help="Mermaid-Diagramm des Zweigbaums")
    mp.add_argument("--all", action="store_true", help="auch verworfene Zweige")
    mp.add_argument("--out", help="Zieldatei statt stdout")
    mp.set_defaults(func=cmd_map)

    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

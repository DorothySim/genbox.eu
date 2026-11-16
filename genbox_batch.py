#!/usr/bin/env python3
"""
genbox_batch.py - batch-process a folder of images with GenBox (genbox.eu).

Checks a directory for images, sends them to one GenBox tool of your choice
(AI Signature Remover, Upscale Image, Image Enhancer, ...), waits between
requests so the free queue does not answer with HTTP 429, and stores every
result in ./out/ next to a JSON file with the job metadata.

Usage:
    python3 genbox_batch.py                    # interactive, current folder
    python3 genbox_batch.py --dir photos/      # another folder
    python3 genbox_batch.py --tool upscale-image --limit 5
    python3 genbox_batch.py --list-tools

Requirements: Python 3.8+, requests (pip install -r requirements.txt)
License: MIT
"""

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Brak biblioteki requests. Zainstaluj: pip install -r requirements.txt")

API_BASE = "https://genbox.eu"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
UA = "genbox-batch/1.0 (+https://genbox.eu)"


def log(msg=""):
    print(msg, flush=True)


def fetch_tools(session, api_base):
    r = session.get(f"{api_base}/api/tools", timeout=30)
    r.raise_for_status()
    data = r.json()
    return [t for t in data.get("tools", []) if t.get("enabled", True)]


def ask(prompt, default=""):
    suffix = f" [{default}]" if default != "" else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else default


def build_params(tool, non_interactive=False):
    """Ask the user for the tool options, keep the tool defaults otherwise."""
    params = {}
    inputs = [i for i in tool.get("inputs", []) if i.get("type") != "file"]
    if non_interactive or not inputs:
        return params

    log("")
    log(f"Opcje narzędzia {tool['name']} (Enter = wartość domyślna):")
    for inp in inputs:
        name = inp.get("name")
        label = inp.get("label", name)
        typ = inp.get("type")
        default = inp.get("default", "")

        if typ == "select":
            options = inp.get("options") or []
            if options and isinstance(options[0], dict):
                values = [o.get("value") for o in options]
                labels = [o.get("label", o.get("value")) for o in options]
            else:
                values = list(options)
                labels = list(options)
            log(f"  {label}:")
            for idx, lab in enumerate(labels, 1):
                mark = " (domyślnie)" if str(values[idx - 1]) == str(default) else ""
                log(f"    {idx}. {lab}{mark}")
            pick = ask(f"  wybór 1-{len(labels)}", "")
            if pick.isdigit() and 1 <= int(pick) <= len(values):
                params[name] = values[int(pick) - 1]
            elif pick:
                params[name] = pick  # accept raw value too
        elif typ == "checkbox":
            cur = "tak" if default else "nie"
            pick = ask(f"  {label} (tak/nie)", cur).lower()
            params[name] = "true" if pick.startswith("t") else ""
        elif typ in ("range", "number"):
            hint = f"{inp.get('min', '')}..{inp.get('max', '')}".strip(".")
            pick = ask(f"  {label} {hint}", str(default))
            if pick != "":
                params[name] = pick
        else:
            continue
    return params


def pick_tool(tools):
    log("")
    log("Dostępne narzędzia GenBox:")
    for idx, tool in enumerate(tools, 1):
        cat = tool.get("category", "")
        log(f"  {idx:2d}. {tool['name']}  [{tool['slug']}]  ({cat})")
    while True:
        pick = ask("Wybierz numer narzędzia", "1")
        if pick.isdigit() and 1 <= int(pick) <= len(tools):
            return tools[int(pick) - 1]
        log("  Nieprawidłowy wybór.")


def collect_files(folder, recursive, videos):
    exts = IMAGE_EXT | (VIDEO_EXT if videos else set())
    pattern = "**/*" if recursive else "*"
    files = [p for p in sorted(Path(folder).glob(pattern))
             if p.is_file() and p.suffix.lower() in exts]
    return files


def already_done(path, out_dir):
    """Treat a file as done when anything with its stem already sits in out/."""
    stem = path.stem
    for existing in out_dir.glob(f"{stem}*"):
        return True
    return False


def sleep_between(base, jitter, first=False):
    if first:
        return
    delay = base + random.uniform(0, max(jitter, 0))
    log(f"  ... czekam {delay:.1f}s (limit darmowej kolejki)")
    time.sleep(delay)


def post_job(session, api_base, tool, path, params, retries=4):
    url = f"{api_base}/api/tools/{tool['slug']}/run"
    attempt = 0
    while True:
        attempt += 1
        try:
            with open(path, "rb") as fh:
                files = {"image": (path.name, fh)}
                resp = session.post(url, files=files, data=params, timeout=300)
        except requests.RequestException as exc:
            log(f"  ! błąd sieci: {exc}")
            if attempt > retries:
                raise
            time.sleep(5 * attempt)
            continue

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 429:
            wait = resp.headers.get("Retry-After")
            wait = float(wait) if wait and wait.replace(".", "", 1).isdigit() else 15 * attempt
            body = resp.text[:200].replace("\n", " ")
            log(f"  ! 429 rate limit (próba {attempt}/{retries}), czekam {wait:.0f}s")
            if attempt > retries:
                raise RuntimeError(f"429 po {retries} próbach: {body}")
            time.sleep(wait)
            continue

        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")


def download(session, url, target, retries=3):
    for attempt in range(1, retries + 1):
        try:
            with session.get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(target, "wb") as fh:
                    for chunk in r.iter_content(1 << 16):
                        fh.write(chunk)
            return target.stat().st_size
        except requests.RequestException as exc:
            if attempt == retries:
                raise
            log(f"  ! pobieranie nie udało się ({exc}), ponawiam")
            time.sleep(3 * attempt)


def process_one(session, api_base, tool, path, out_dir, params):
    log(f"- {path.name}")
    data = post_job(session, api_base, tool, path, params)
    job = data.get("job", "job")
    written = []

    for out in data.get("outputs", []):
        url = out.get("url")
        name = out.get("name") or f"{path.stem}-{job}{path.suffix}"
        if not url:
            continue
        target = out_dir / name
        size = download(session, url, target)
        written.append({"file": target.name, "bytes": size, "url": url})
        log(f"    -> out/{target.name} ({size} B)")

    meta = {
        "source": path.name,
        "tool": tool["slug"],
        "job": job,
        "params": params,
        "notes": data.get("notes"),
        "zip": data.get("zip"),
        "outputs": written,
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    meta_path = out_dir / f"{path.stem}.{tool['slug']}.{job}.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main():
    ap = argparse.ArgumentParser(description="Batch image processing via genbox.eu")
    ap.add_argument("--dir", default=".", help="folder with images (default: current)")
    ap.add_argument("--out", default="out", help="output folder (default: out)")
    ap.add_argument("--tool", help="tool slug, e.g. upscale-image (skips the menu)")
    ap.add_argument("--limit", type=int, default=5, help="max files per run (default: 5, 0 = all)")
    ap.add_argument("--sleep", type=float, default=8.0, help="base delay between files in s (default: 8)")
    ap.add_argument("--jitter", type=float, default=3.0, help="random extra delay in s (default: 3)")
    ap.add_argument("--recursive", action="store_true", help="walk subfolders")
    ap.add_argument("--overwrite", action="store_true", help="process files already present in out/")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, send nothing")
    ap.add_argument("--list-tools", action="store_true", help="list tools and exit")
    ap.add_argument("--api-base", default=API_BASE, help=f"API base (default: {API_BASE})")
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    tools = fetch_tools(session, args.api_base)
    if args.list_tools:
        for tool in tools:
            log(f"{tool['slug']:26s} {tool['name']}")
        return

    if args.tool:
        tool = next((t for t in tools if t["slug"] == args.tool), None)
        if not tool:
            sys.exit(f"Nieznane narzędzie: {args.tool}. Sprawdź --list-tools")
    else:
        tool = pick_tool(tools)

    params = build_params(tool, non_interactive=False)

    out_dir = Path(args.out)
    out_dir.mkdir(exist_ok=True)

    videos = "video" in tool["slug"] or any(
        "video/mp4" in str(i.get("accept")) for i in tool.get("inputs", []))
    files = collect_files(args.dir, args.recursive, videos)
    if not args.overwrite:
        files = [f for f in files if not already_done(f, out_dir)]
    if args.limit and args.limit > 0:
        files = files[: args.limit]

    if not files:
        log("Brak plików do przetworzenia.")
        return

    log("")
    log(f"Narzędzie: {tool['name']} ({tool['slug']})")
    log(f"Pliki ({len(files)}): {', '.join(f.name for f in files)}")
    log(f"Wyniki: {out_dir.resolve()}")
    if args.dry_run:
        log("DRY RUN - nic nie wysyłam.")
        return

    ok = 0
    for idx, path in enumerate(files):
        sleep_between(args.sleep, args.jitter, first=(idx == 0))
        try:
            process_one(session, args.api_base, tool, path, out_dir, params)
            ok += 1
        except Exception as exc:  # keep going, report at the end
            log(f"  ! {path.name}: {exc}")

    log("")
    log(f"Gotowe: {ok}/{len(files)} plików. Wyniki w {out_dir.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\nPrzerwane.")

#!/usr/bin/env python3
"""
genbox_batch.py - batch-process a folder of images with GenBox (genbox.eu).

Picks one image tool (AI Signature Remover, Upscale Image or Image Enhancer),
sends every image from a folder one by one, keeps a delay between requests so
the free queue does not answer with HTTP 429, and stores all results in ./out/
together with a JSON file per job.

A GenBox config profile (the JSON GenBox saves for each job) can be passed with
--config, either as a local file, a URL or a bare job id; the script then reuses
those exact settings instead of asking for them.

Usage:
    python3 genbox_batch.py
    python3 genbox_batch.py --dir photos --tool upscale-image
    python3 genbox_batch.py --dir photos --tool upscale-image --config profile.json
    python3 genbox_batch.py --list-tools

Requirements: Python 3.8+, requests (pip install -r requirements.txt)
License: MIT
"""

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: requests. Install it with: pip install -r requirements.txt")

API_BASE = "https://genbox.eu"
UA = "genbox-batch/1.1 (+https://genbox.eu)"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}

# Tools that make sense for a folder of images.
IMAGE_TOOLS = (
    "signature-remover",
    "upscale-image",
    "image-enhancer",
)


def log(msg=""):
    print(msg, flush=True)


def ask(prompt, default=""):
    suffix = f" [{default}]" if default != "" else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return value if value else default


def ask_yes(prompt, default=True):
    hint = "Y/n" if default else "y/N"
    value = ask(f"{prompt} ({hint})", "y" if default else "n").lower()
    return value.startswith("y")


def fetch_tools(session, api_base):
    resp = session.get(f"{api_base}/api/tools", timeout=30)
    resp.raise_for_status()
    tools = [t for t in resp.json().get("tools", []) if t.get("enabled", True)]
    return tools


def option_values(inp):
    """Return (values, labels) for select/tags inputs."""
    options = inp.get("options") or []
    if options and isinstance(options[0], dict):
        values = [o.get("value") for o in options]
        labels = [str(o.get("label", o.get("value"))) for o in options]
    else:
        values = list(options)
        labels = [str(o) for o in options]
    return values, labels


def build_params(tool, interactive=True):
    """Collect the tool options. With interactive=False every default is kept."""
    params = {}
    inputs = [i for i in tool.get("inputs", []) if i.get("type") != "file"]
    if not inputs:
        return params

    if interactive:
        log("")
        log(f"Options for {tool['name']} (press Enter to keep the default):")

    for inp in inputs:
        name = inp.get("name")
        label = inp.get("label", name)
        typ = inp.get("type")
        default = inp.get("default", "")

        if typ in ("select", "tags"):
            values, labels = option_values(inp)
            if not values:
                continue
            default_idx = 1
            for idx, val in enumerate(values, 1):
                if str(val) == str(default):
                    default_idx = idx

            if interactive:
                log(f"  {label}:")
                for idx, lab in enumerate(labels, 1):
                    mark = " (default)" if idx == default_idx else ""
                    log(f"    {idx}. {lab}{mark}")
                pick = ask(f"  choice 1-{len(values)}", str(default_idx))
            else:
                pick = str(default_idx)

            if pick.isdigit() and 1 <= int(pick) <= len(values):
                params[name] = values[int(pick) - 1]
            elif pick:
                params[name] = pick

        elif typ == "checkbox":
            current = "yes" if default else "no"
            if interactive:
                pick = ask(f"  {label}", current).lower()
            else:
                pick = current
            params[name] = "true" if pick.startswith("y") else ""

        elif typ in ("range", "number"):
            hint = ""
            if inp.get("min") is not None and inp.get("max") is not None:
                hint = f" ({inp['min']}..{inp['max']})"
            if interactive:
                pick = ask(f"  {label}{hint}", str(default))
            else:
                pick = str(default)
            if pick != "":
                params[name] = pick

    return params


def pick_tool(tools, show_all=False):
    visible = [t for t in tools if t["slug"] in IMAGE_TOOLS] if not show_all else tools
    if not visible:
        visible = tools
    log("")
    log("Available image tools:")
    for idx, tool in enumerate(visible, 1):
        log(f"  {idx}. {tool['name']}  [{tool['slug']}]")
    while True:
        pick = ask("Pick a tool", "1")
        if pick.isdigit() and 1 <= int(pick) <= len(visible):
            return visible[int(pick) - 1]
        log("  Invalid choice.")


def load_profile(source, tool_slug=None, api_base=API_BASE, session=None):
    """Load a GenBox config profile: local file, URL or bare job id."""
    if source.startswith("http://") or source.startswith("https://"):
        getter = session.get if session else requests.get
        resp = getter(source, timeout=30)
        resp.raise_for_status()
        return resp.json()

    path = Path(source)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    if not tool_slug:
        raise SystemExit("--config with a job id needs --tool <slug> as well")
    url = f"{api_base}/config/{source}/genbox-config-{tool_slug}.json"
    getter = session.get if session else requests.get
    resp = getter(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def collect_files(folder, recursive, videos):
    exts = IMAGE_EXT | (VIDEO_EXT if videos else set())
    pattern = "**/*" if recursive else "*"
    return [p for p in sorted(Path(folder).glob(pattern))
            if p.is_file() and p.suffix.lower() in exts]


def file_sha1(path):
    digest = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def already_done(path, sha1, out_dir, tool_slug):
    """Done = a metadata file for this stem + tool exists with the same content hash.

    Files processed by older versions (no sha1 stored) are treated as done only
    when the stem matches, so nothing is silently re-uploaded twice.
    """
    for meta in out_dir.glob(f"{path.stem}.{tool_slug}.*.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            return True
        stored = data.get("source_sha1")
        if stored is None or stored == sha1:
            return True
    return False


def sleep_between(base, jitter, first=False):
    if first:
        return
    delay = base + random.uniform(0, max(jitter, 0))
    log(f"  ... waiting {delay:.1f}s (free queue limit)")
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
            log(f"  ! network error: {exc}")
            if attempt > retries:
                raise
            time.sleep(5 * attempt)
            continue

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 429:
            header = resp.headers.get("Retry-After")
            wait = float(header) if header and header.replace(".", "", 1).isdigit() else 15 * attempt
            body = resp.text[:200].replace("\n", " ")
            log(f"  ! 429 rate limited (attempt {attempt}/{retries}), sleeping {wait:.0f}s")
            if attempt > retries:
                raise RuntimeError(f"429 after {retries} attempts: {body}")
            time.sleep(wait)
            continue

        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")


def download(session, url, target, retries=3):
    for attempt in range(1, retries + 1):
        try:
            with session.get(url, stream=True, timeout=300) as resp:
                resp.raise_for_status()
                with open(target, "wb") as fh:
                    for chunk in resp.iter_content(1 << 16):
                        fh.write(chunk)
            return target.stat().st_size
        except requests.RequestException as exc:
            if attempt == retries:
                raise
            log(f"  ! download failed ({exc}), retrying")
            time.sleep(3 * attempt)


def process_one(session, api_base, tool, path, out_dir, params):
    log(f"- {path.name}")
    data = post_job(session, api_base, tool, path, params)
    job = data.get("job", "job")
    written = []

    for item in data.get("outputs", []):
        url = item.get("url")
        name = item.get("name") or f"{path.stem}-{job}{path.suffix}"
        if not url:
            continue
        target = out_dir / name
        size = download(session, url, target)
        written.append({"file": target.name, "bytes": size, "url": url})
        log(f"    -> out/{target.name} ({size} B)")

    meta = {
        "source": path.name,
        "source_sha1": file_sha1(path),
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
    ap = argparse.ArgumentParser(
        description="Batch image processing with the free tools at genbox.eu")
    ap.add_argument("--dir", default=".", help="folder with images (default: current)")
    ap.add_argument("--out", help="output folder (default: <dir>/out)")
    ap.add_argument("--tool", help="tool slug: signature-remover, upscale-image, image-enhancer")
    ap.add_argument("--config", help="GenBox config profile: local JSON file, URL or job id")
    ap.add_argument("--limit", type=int, default=5, help="max files per run (default: 5, 0 = all)")
    ap.add_argument("--sleep", type=float, default=8.0, help="base delay between files in s (default: 8)")
    ap.add_argument("--jitter", type=float, default=3.0, help="random extra delay in s (default: 3)")
    ap.add_argument("--recursive", action="store_true", help="walk subfolders")
    ap.add_argument("--overwrite", action="store_true", help="process files that already have output")
    ap.add_argument("--yes", action="store_true", help="keep every default, ask nothing")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, upload nothing")
    ap.add_argument("--list-tools", action="store_true", help="list tools and exit")
    ap.add_argument("--all-tools", action="store_true", help="show every GenBox tool, not only image tools")
    ap.add_argument("--api-base", default=API_BASE, help=f"API base (default: {API_BASE})")
    args = ap.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    tools = fetch_tools(session, args.api_base)
    if args.list_tools:
        for tool in tools:
            mark = "*" if tool["slug"] in IMAGE_TOOLS else " "
            log(f"{mark} {tool['slug']:26s} {tool['name']}")
        log("")
        log("* = image tool offered in the menu (use --all-tools to show the rest)")
        return

    if args.tool:
        tool = next((t for t in tools if t["slug"] == args.tool), None)
        if not tool:
            sys.exit(f"Unknown tool: {args.tool}. Run --list-tools to see the slugs.")
    else:
        tool = pick_tool(tools, show_all=args.all_tools)

    params = {}
    if args.config:
        profile = load_profile(args.config, tool["slug"], args.api_base, session)
        params = {k: v for k, v in profile.items()
                  if not k.startswith("_") and k != "image"}
        log(f"Loaded config profile: {args.config}")
        if params:
            log("  " + json.dumps(params, ensure_ascii=False))
    if not params:
        params = build_params(tool, interactive=not args.yes)

    out_dir = Path(args.out) if args.out else Path(args.dir) / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    has_video = any("video/" in str(i.get("accept", "")) for i in tool.get("inputs", []))
    found = collect_files(args.dir, args.recursive, has_video)
    skipped = []
    if not args.overwrite:
        for f in found:
            if already_done(f, file_sha1(f), out_dir, tool["slug"]):
                skipped.append(f)
        files = [f for f in found if f not in skipped]
    else:
        files = list(found)
    if args.limit and args.limit > 0:
        files = files[: args.limit]

    log("")
    log(f"Found {len(found)} file(s) in {Path(args.dir).resolve()}")
    if skipped:
        log(f"Skipped {len(skipped)} already processed with {tool['slug']} "
            f"(use --overwrite to redo them)")

    if not files:
        if found:
            log("Nothing to process: every file already has a result for this tool.")
        else:
            log("Nothing to process: no images found. "
                "Supported types: " + ", ".join(sorted(IMAGE_EXT)) +
                ". Use --recursive to include subfolders.")
        return

    log("")
    log(f"Tool: {tool['name']} ({tool['slug']})")
    log(f"Files ({len(files)}): {', '.join(f.name for f in files)}")
    log(f"Output: {out_dir.resolve()}")
    if args.dry_run:
        log("DRY RUN - nothing uploaded.")
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
    log(f"Done: {ok}/{len(files)} files. Results in {out_dir.resolve()}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("\nInterrupted.")

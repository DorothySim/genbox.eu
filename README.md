# genbox.eu — batch image tools

A small, dependency-light Python script that takes a folder of images and runs them through the free tools at [genbox.eu](https://genbox.eu): AI signature removal, upscaling, image enhancing and more. Results land in `out/`, one JSON file per job keeps the metadata.

> 🇵🇱 Krótko: skrypt bierze obrazki z katalogu, pyta w konsoli które narzędzie GenBox użyć, wysyła pliki po kolei (z przerwami, żeby nie dostać 429) i zapisuje wyniki do `out/`.

---

## What is GenBox?

GenBox (https://genbox.eu) is a set of free online tools for video, graphics and music. No account, no watermark, no signup: you drop a file, pick options, and get the result back.

Tool families:

| Category | Tools |
| --- | --- |
| Cleanup & fix | AI Signature Remover, Upscale Image, Image Enhancer |
| Generate | AI Image Generator, Advanced AI Image Generator, Brand Tile Generator, Meme Generator |
| Audio & video | Music Video Generator, Video and Music Mix, Beat Audio Slicer |

The tools are designed for the boring-but-necessary work before publishing: stripping AI metadata and signatures, upscaling small assets, normalising colour, sharpening, adding realistic grain, generating crops for social formats.

## Public API

GenBox exposes a small JSON API that the website itself uses. Everything is unauthenticated and free, which also means it is rate limited.

| Method | Path | What it does |
| --- | --- | --- |
| `GET` | `/api/health` | service status, tool count, version |
| `GET` | `/api/tools` | full catalogue: slugs, inputs, options, defaults |
| `POST` | `/api/tools/<slug>/run` | `multipart/form-data` with the file in the `image` field plus tool options, returns JSON |

Example:

```bash
curl -X POST https://genbox.eu/api/tools/upscale-image/run \
  -F "image=@photo.png" \
  -F "factor=x2"
```

```json
{
  "job": "20261116-201226-eoidjw",
  "notes": "photo.png: 64x64 -> 128x128, AI 4x (Remacri)",
  "outputs": [
    { "name": "photo-x2.png", "size": "1.3 KB", "url": "https://genbox.eu/out/<job>/photo-x2.png" }
  ],
  "zip": "https://genbox.eu/out/<job>/download.zip",
  "config_url": "https://genbox.eu/config/<job>/genbox-config-upscale-image.json"
}
```

Options are per tool and self-documented by `/api/tools`:

```bash
curl -s https://genbox.eu/api/tools | python3 -m json.tool | less
```

For example `upscale-image` accepts `factor=x2|x4`, `signature-remover` accepts `credit=on|off` plus a long list of grade/noise/JPEG knobs, and `image-enhancer` accepts `size_mode=original|x2|x4`, `crop_ratio`, `use_upscaler` and the same grading set.

### Rate limits

The free queue is shared, so `429` is normal when you push too hard:

```json
{"error": "rate limited", "bucket": "api", "label": "API limit",
 "message": "The free queue is busy. ...", "mode": "gate"}
```

Some tools additionally gate content that needs an age check (`"age_required": true`), which cannot be bypassed from a script. Handle it as a skip, not a retry loop.

The script therefore sleeps between files (default `8s` + up to `3s` jitter), retries `429` with `Retry-After` when present, and backs off exponentially otherwise.

## The script

`genbox_batch.py` — interactive by default, scriptable with flags.

```bash
pip install -r requirements.txt
python3 genbox_batch.py
```

What it does:

1. reads the tool catalogue from `/api/tools`,
2. asks which tool to use (or takes `--tool`),
3. asks for the tool options (Enter keeps the tool default),
4. collects images from the folder (`jpg jpeg png webp bmp tif tiff`, plus `mp4 mov m4v` for video-capable tools),
5. skips files that already have a result in `out/`,
6. uploads up to `--limit` files (default `5`) one by one, with a delay between requests,
7. downloads every output into `out/` and writes `out/<file>.<tool>.<job>.json`.

```bash
# plain run, 5 images from the current folder
python3 genbox_batch.py

# non-interactive: upscale everything in photos/ by 4x, 3 s between files
python3 genbox_batch.py --dir photos --tool upscale-image --limit 0 --sleep 3

# see what is available
python3 genbox_batch.py --list-tools

# dry run: plan only
python3 genbox_batch.py --dir photos --tool image-enhancer --dry-run
```

Flags: `--dir`, `--out`, `--tool`, `--limit`, `--sleep`, `--jitter`, `--recursive`, `--overwrite`, `--dry-run`, `--list-tools`, `--api-base`.

### Notes

- `--limit 0` means "everything".
- `--overwrite` re-processes files whose name is already present in `out/`.
- `--recursive` walks subfolders.
- The script never deletes anything; `out/` only grows.
- `credit=on` (the GenBox default) stamps "GenBox Free Tools (genbox.eu)" as the author. Turn it off with `--tool` options if you own the source material.

## Requirements

Python 3.8+ and `requests`. That is it.

## Disclaimer

GenBox is a free service. Be polite: keep the delay between requests, do not parallelise a hundred uploads, and respect the rate limits instead of hammering them. Only process files you have the rights to.

## License

MIT — see [LICENSE](LICENSE).

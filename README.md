# genbox.eu — free image batch processing

A small Python script that takes a folder of images and runs them through the free image tools at [genbox.eu](https://genbox.eu): AI signature removal, upscaling and image enhancing. Results land in `out/`, one JSON file per job keeps the metadata.

## What is GenBox?

[GenBox](https://genbox.eu) is a set of free online tools for video, graphics and music. No account, no watermark: you drop a file, pick options, and get the result back.

The tools that make sense for still images, and the ones this script focuses on:

| Tool | Slug | What it does |
| --- | --- | --- |
| AI Signature Remover | `signature-remover` | strips AI metadata and signatures, optional grading, grain, JPEG history |
| Upscale Image | `upscale-image` | 2x or 4x upscale with the Remacri model |
| Image Enhancer | `image-enhancer` | crop to social ratios, optional upscale, grading, sharpen, noise |

The other GenBox tools (image generators, meme generator, brand tiles, music video, audio slicer) are not a good fit for a folder of existing images, so the menu hides them by default. `--all-tools` shows the full catalogue.

## Install

```bash
git clone https://github.com/DorothySim/genbox.eu.git
cd genbox.eu
pip install -r requirements.txt
```

## Usage examples

Interactive run on the current folder (asks for the tool and its options):

```bash
python3 genbox_batch.py
```

Upscale every image in `photos/` by 4x, three seconds between files, all files (no 5-file cap):

```bash
python3 genbox_batch.py --dir photos --tool upscale-image --limit 0 --sleep 3
```

Strip AI signatures from up to 5 files, keep the GenBox credit stamp off:

```bash
python3 genbox_batch.py --dir photos --tool signature-remover
# when asked for "Genbox credit" pick 2 (off)
```

Reuse a GenBox config profile saved by the website, so the run matches exactly what you set in the UI:

```bash
# from a saved JSON file
python3 genbox_batch.py --dir photos --tool upscale-image --config profile.json

# straight from the GenBox config URL (the one in config_url of a previous run)
python3 genbox_batch.py --dir photos --tool upscale-image \
  --config https://genbox.eu/config/20261116-201226-eoidjw/genbox-config-upscale-image.json

# or just by job id, the script builds the URL itself
python3 genbox_batch.py --dir photos --tool upscale-image --config 20261116-201226-eoidjw
```

Preview what would happen without uploading anything:

```bash
python3 genbox_batch.py --dir photos --tool image-enhancer --dry-run
```

Fully non-interactive run that keeps every default (useful from cron):

```bash
python3 genbox_batch.py --dir photos --tool upscale-image --yes --limit 20 --sleep 10
```

List the tool slugs, image tools first:

```bash
python3 genbox_batch.py --list-tools
```

## What the script does

1. reads the catalogue from `/api/tools`,
2. asks which image tool to use (or takes `--tool`),
3. either loads a saved config profile (`--config`) or asks for the options, with Enter keeping the tool default,
4. collects images from the folder (`jpg jpeg png webp bmp tif tiff`, plus `mp4 mov m4v` for video-capable tools),
5. skips files that already have a result in `out/` unless `--overwrite`,
6. uploads up to `--limit` files (default `5`) one by one, with a delay between requests,
7. downloads every output into `out/` and writes `out/<file>.<tool>.<job>.json`.

Flags: `--dir`, `--out`, `--tool`, `--config`, `--limit`, `--sleep`, `--jitter`, `--recursive`, `--overwrite`, `--yes`, `--dry-run`, `--list-tools`, `--all-tools`, `--api-base`.

## Config profiles

Every GenBox job stores the settings it ran with, for example:

```json
{
  "factor": "x2",
  "_uploads": { "image": ["photo.png"] }
}
```

`--config` accepts that JSON as a local file, as a URL, or as a bare job id (then `--tool` is required). Keys starting with `_` (`_uploads` and friends) are ignored, the rest is sent as the request parameters on top of the file upload.

## Public API

GenBox exposes a small JSON API that the website itself uses. Everything is unauthenticated and free, which also means it is rate limited.

| Method | Path | What it does |
| --- | --- | --- |
| `GET` | `/api/health` | service status, tool count, version |
| `GET` | `/api/tools` | full catalogue: slugs, inputs, options, defaults |
| `POST` | `/api/tools/<slug>/run` | `multipart/form-data` with the file in the `image` field plus tool options, returns JSON |

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

### Rate limits

The free queue is shared, so `429` is normal when you push too hard:

```json
{"error": "rate limited", "bucket": "api", "label": "API limit",
 "message": "The free queue is busy. ...", "mode": "gate"}
```

Some tools additionally gate content that needs an age check (`"age_required": true`), which cannot be bypassed from a script. The script treats it as a failed file and moves on instead of retrying in a loop.

## Requirements

Python 3.8+ and `requests`. That is it.

## Disclaimer

GenBox is a free service. Be polite: keep the delay between requests, do not parallelise a hundred uploads, and respect the rate limits instead of hammering them. Only process files you have the rights to.

## License

MIT — see [LICENSE](LICENSE).

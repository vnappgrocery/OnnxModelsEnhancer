#!/usr/bin/env python3
"""
download_mozilla_models.py

Downloads the biggest "stable released" Firefox Translations model for every
language that has an English<->lang pair, from Mozilla's public model
registry (the same GCS bucket that powers Firefox's built-in translation).

Registry JSON (source of truth):
    https://storage.googleapis.com/moz-fx-translations-data--303e-prod-translations-data/db/models.json

For every language pair (e.g. "en-fr", "fr-en") the registry lists one or
more trained model "entries" (different architectures / training runs).
Each entry has a `releaseStatus` field:
    - null        -> experimental / not shipped
    - "Nightly"   -> shipped to Nightly only, not considered "stable"
    - "Release"*  -> shipped to a stable channel (Release, Release Desktop,
                     Release Android, ...) -> this is what we want

Among the entries that are "stable released", we keep the *biggest* one
(largest model file, which correlates with translation quality/capacity).

Output layout (inside MAIN_FOLDER, customizable below):

    mozilla_models/
        fr/
            fren/   <- model+lex+vocab for fr -> en
            enfr/   <- model+lex+vocab for en -> fr
        zh/
            zhen/
            enzh/
        ...

Some language pairs use a single shared "vocab" file, others (mostly CJK
languages) use two separate "srcVocab"/"trgVocab" files -- the script
handles both cases automatically and downloads whatever vocab file(s)
the registry lists for that model.

Requires: requests   (pip install requests)
"""

from __future__ import annotations

import gzip
import shutil
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests

# --------------------------------------------------------------------------- #
# CONFIGURATION - edit these to taste
# --------------------------------------------------------------------------- #

# The root folder everything will be written under. Can be relative or
# absolute. This is the single variable you need to change to move the
# whole download elsewhere.
MAIN_FOLDER = Path("MozillaModelsDownloader/mozilla_models")
 
# Where to read the model registry from.
MODELS_JSON_URL = (
    "https://storage.googleapis.com/"
    "moz-fx-translations-data--303e-prod-translations-data/db/models.json"
)
 
# If True, .gz files are decompressed after download and the .gz is removed,
# leaving plain files (model.xxen.intgemm.alphas.bin, vocab.xxen.spm, ...)
# ready to be used directly. If False, the raw .gz files are kept as-is.
DECOMPRESS = True
 
# Set to True to re-download files that already exist locally.
OVERWRITE_EXISTING = False
 
# Where the per-language .zip archives are written by compress_language_folders().
# Can be relative or absolute, and independent from MAIN_FOLDER.
COMPRESSED_FOLDER = Path("MozillaModelsDownloader/mozilla_models_compressed")
 
# Set to True to re-create .zip archives that already exist in COMPRESSED_FOLDER.
OVERWRITE_EXISTING_ZIPS = False

# --------------------------------------------------------------------------- #


def log(msg: str) -> None:
    print(msg, flush=True)


def fetch_registry(url: str) -> dict:
    log(f"Fetching model registry: {url}")
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def is_stable_release(release_status) -> bool:
    """A model is considered 'stable released' if it has a releaseStatus
    that isn't empty/null and isn't Nightly-only."""
    if not release_status:
        return False
    return "nightly" not in release_status.lower()


def model_size(entry: dict) -> int:
    """Best-effort 'size' of a model entry, used to pick the biggest one."""
    files = entry.get("files", {})
    model_file = files.get("model", {})
    size = model_file.get("uncompressedSize")
    if isinstance(size, int):
        return size
    # Fallback: use parameter count if the byte size isn't available.
    stats = entry.get("modelStatistics", {})
    params = stats.get("parameters")
    if isinstance(params, int):
        return params
    return 0


def pick_best_stable_entry(entries: list[dict]) -> dict | None:
    stable = [e for e in entries if is_stable_release(e.get("releaseStatus"))]
    if not stable:
        return None
    return max(stable, key=model_size)


def download_file(base_url: str, rel_path: str, dest_dir: Path) -> None:
    """Download base_url/rel_path into dest_dir, optionally decompressing
    a trailing .gz on the fly."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    url = urljoin(base_url + "/", rel_path)
    filename = rel_path.split("/")[-1]
    gz_path = dest_dir / filename
    final_path = gz_path.with_suffix("") if (DECOMPRESS and filename.endswith(".gz")) else gz_path

    if final_path.exists() and not OVERWRITE_EXISTING:
        log(f"    already have {final_path.name}, skipping")
        return

    log(f"    downloading {filename}")
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(gz_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

    if DECOMPRESS and filename.endswith(".gz"):
        log(f"    decompressing {filename}")
        with gzip.open(gz_path, "rb") as f_in, open(final_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        gz_path.unlink()


def download_entry_files(entry: dict, base_url: str, dest_dir: Path) -> None:
    files = entry.get("files", {})
    # files can contain: "model", "lexicalShortlist", and either "vocab"
    # or the pair "srcVocab"/"trgVocab". Just download whatever file
    # objects are present.
    for _key, file_info in files.items():
        rel_path = file_info.get("path")
        if not rel_path:
            continue
        download_file(base_url, rel_path, dest_dir)


def compress_language_folders(
    source_folder: Path = MAIN_FOLDER,
    dest_folder: Path = COMPRESSED_FOLDER,
) -> None:
    """Scan `source_folder` (the MAIN_FOLDER produced by main()) and, for
    every language subfolder that contains BOTH of its "<lang>en" and
    "en<lang>" subfolders, create a zip copy of that whole language folder
    inside `dest_folder`, named "Mozilla_<lang>.zip".
 
    Language folders that are missing one of the two direction subfolders
    (e.g. only "zhen" but no "enzh") are skipped, since the pair is
    incomplete.
    """
    source_folder = Path(source_folder)
    dest_folder = Path(dest_folder)
 
    if not source_folder.is_dir():
        log(f"Source folder {source_folder} does not exist, nothing to compress.")
        return
 
    dest_folder.mkdir(parents=True, exist_ok=True)
 
    compressed = []
    skipped = []
 
    for lang_dir in sorted(source_folder.iterdir()):
        if not lang_dir.is_dir():
            continue
 
        lang = lang_dir.name
        required = {f"{lang}en", f"en{lang}"}
        present = {d.name for d in lang_dir.iterdir() if d.is_dir()}
 
        if not required.issubset(present):
            missing = required - present
            log(f"[{lang}] missing subfolder(s) {sorted(missing)}, skipping")
            skipped.append(lang)
            continue
 
        zip_base_name = dest_folder / f"Mozilla_{lang}"
        zip_path = zip_base_name.with_suffix(".zip")
 
        if zip_path.exists() and not OVERWRITE_EXISTING_ZIPS:
            log(f"[{lang}] {zip_path.name} already exists, skipping")
            compressed.append(lang)
            continue
 
        log(f"[{lang}] zipping -> {zip_path}")
        # root_dir=lang_dir.parent + base_dir=lang_dir.name makes the zip's
        # top-level entry the language folder itself (a true "copy" of it),
        # e.g. Mozilla_zh.zip -> zh/zhen/..., zh/enzh/...
        shutil.make_archive(
            base_name=str(zip_base_name),
            format="zip",
            root_dir=str(lang_dir.parent),
            base_dir=lang_dir.name,
        )
        compressed.append(lang)
 
    log("")
    log(f"Compression done. Zipped: {len(compressed)} ({', '.join(compressed) or '-'})")
    if skipped:
        log(f"Skipped (incomplete pair): {len(skipped)} ({', '.join(skipped)})")


def main() -> None:
    registry = fetch_registry(MODELS_JSON_URL)
    base_url = registry.get("baseUrl") or MODELS_JSON_URL.rsplit("/db/", 1)[0]
    models = registry.get("models", {})

    MAIN_FOLDER.mkdir(parents=True, exist_ok=True)

    skipped_pairs = []
    processed_langs = set()

    for pair_key, entries in sorted(models.items()):
        if "-" not in pair_key:
            continue
        src, trg = pair_key.split("-", 1)

        # We only care about pairs that involve English on one side.
        if src == "en" and trg != "en":
            lang = trg
            folder_name = "en" + lang
        elif trg == "en" and src != "en":
            lang = src
            folder_name = lang + "en"
        else:
            continue  # skip en-en or non-English pairs, if any

        best = pick_best_stable_entry(entries)
        if best is None:
            skipped_pairs.append(pair_key)
            log(f"[{pair_key}] no stable released model found, skipping")
            continue

        lang_dir = MAIN_FOLDER / lang
        dest_dir = lang_dir / folder_name

        arch = best.get("architecture")
        status = best.get("releaseStatus")
        log(f"[{pair_key}] using architecture={arch!r} releaseStatus={status!r} -> {dest_dir}")

        download_entry_files(best, base_url, dest_dir)
        processed_langs.add(lang)

    log("")
    log(f"Done. Languages processed: {len(processed_langs)}")
    if skipped_pairs:
        log(f"Pairs with no stable released model ({len(skipped_pairs)}): {', '.join(skipped_pairs)}")


if __name__ == "__main__":
    try:
        #main()
        compress_language_folders()
    except requests.RequestException as exc:
        log(f"Network error: {exc}")
        sys.exit(1)
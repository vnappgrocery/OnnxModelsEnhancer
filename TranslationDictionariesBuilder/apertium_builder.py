from collections import defaultdict
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

import pycountry

from TranslationDictionariesBuilder.Protobuf import dictionary_data_pb2
from TranslationDictionariesBuilder.tools import create_and_define_database, _normalize_text, extract_surface_text, find_first_child, get_lang_codes, local_name, read_pairs_from_db, build_bidirectional_dicts, upsert_dictionary

ORG = "apertium"
OUTDIR = "TranslationDictionariesBuilder/apertium-english-bidix"

# Optional: set a GitHub token to avoid low API limits
# Windows PowerShell:
#   $env:GITHUB_TOKEN="your_token"
# Linux/macOS/Git Bash:
#   export GITHUB_TOKEN="your_token"
GITHUB_TOKEN = None
if(os.path.isfile("TranslationDictionariesBuilder/github_token.txt")):
    GITHUB_TOKEN = open("TranslationDictionariesBuilder/github_token.txt").read()


# Downloader

def api_get(url):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "ApertiumDownloader/1.0")
    req.add_header("Accept", "application/vnd.github+json")
    if GITHUB_TOKEN:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")

    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read().decode("utf-8")
            headers = dict(resp.headers.items())
            return json.loads(data), headers
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP error {e.code} for {url}", file=sys.stderr)
        if body:
            print(body[:500], file=sys.stderr)
        return None, {}
    except Exception as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None, {}


def raw_download(url, dest):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "ApertiumDownloader/1.0")
    if GITHUB_TOKEN:
        req.add_header("Authorization", f"Bearer {GITHUB_TOKEN}")

    with urllib.request.urlopen(req) as resp:
        data = resp.read()

    with open(dest, "wb") as f:
        f.write(data)


def list_org_repos():
    repos = []
    page = 1

    while True:
        url = f"https://api.github.com/orgs/{ORG}/repos?per_page=100&page={page}"
        data, headers = api_get(url)

        if not data:
            break
        if not isinstance(data, list) or len(data) == 0:
            break

        repos.extend(data)

        remaining = headers.get("X-RateLimit-Remaining")
        if remaining == "0":
            print("GitHub API rate limit reached.", file=sys.stderr)
            break

        page += 1
        time.sleep(0.2)

    return repos


def download_apertium():
    os.makedirs(OUTDIR, exist_ok=True)

    print("Listing repositories...")
    repos = list_org_repos()

    if not repos:
        print("No repositories found.")
        return

    for repo in repos:
        name: str = repo.get("name", "")
        pair = name.removeprefix("apertium-")

        default_branch = repo.get("default_branch", "")
        if not name or not default_branch:
            continue

        # Keep only repos whose name contains "eng" or "en"
        if not any(x in name.lower() for x in ["eng", "-en", "en-"]):
            continue

        if(len(pair.split("-")) < 2):
            continue
        srcLang = pair.split("-")[0]
        tgtLang = pair.split("-")[1]
        srcLangCodes = get_lang_codes(srcLang)
        tgtLangCodes = get_lang_codes(tgtLang)

        if(None in srcLangCodes or None in tgtLangCodes):
            continue

        pairs = [
            srcLangCodes[0]+"-"+tgtLangCodes[0],
            tgtLangCodes[0]+"-"+srcLangCodes[0],
            srcLangCodes[1]+"-"+tgtLangCodes[1],
            tgtLangCodes[1]+"-"+srcLangCodes[1],
        ]

        print(f"Checking {name} ...")

        tree_url = f"https://api.github.com/repos/{ORG}/{name}/git/trees/{default_branch}"  # we consider only the files in the top level (root) or the repo, if you want to check all file attach "?recursive=1" at the end of url and remove the file skip inside the loop
        tree_data, headers = api_get(tree_url)

        if not tree_data or "tree" not in tree_data:
            print("Could not read repository tree")
            continue

        dix_paths = []
        for item in tree_data.get("tree", []):
            if item.get("type") != "blob":
                continue

            path: str = item.get("path", "")

            # Skip files not in the root directory
            if "/" in path:
                continue

            if path.lower().endswith(".dix"):
                filename = os.path.basename(path)
                file_type = filename.split(".")[1]  #we extract second part of the name (ex. apertium-srcLang-tgtLang.srcLang-tgtLang.dix), after the first dot, that indicates the language pair of the dix file
                # We skip the dictinaries files that don't have lang pairs in the second part of the name (ex. apertium-srcLang-tgtLang.srcLang-tgtLang.dix)
                if not any(x == file_type.lower() for x in pairs):
                    continue

                dix_paths.append(path)

        if not dix_paths:
            print("  No .dix files found")
            continue

        repo_out = os.path.join(OUTDIR, name)
        os.makedirs(repo_out, exist_ok=True)

        for path in dix_paths:
            filename = os.path.basename(path)
            dest = os.path.join(repo_out, filename)
            raw_url = f"https://raw.githubusercontent.com/{ORG}/{name}/{default_branch}/{path}"

            try:
                print(f"  Downloading {filename}")
                raw_download(raw_url, dest)
            except Exception as e:
                print(f"  Failed to download {filename}: {e}")

        remaining = headers.get("X-RateLimit-Remaining")
        if remaining == "0":
            print("GitHub API rate limit reached.", file=sys.stderr)
            break

        time.sleep(0.2)

    Path(OUTDIR + "/apertium-mkd-eng/apertium-mkd-eng.mkd-eng.alpha.dix").unlink()  # remove the alpha dix file

    print("\nDone.")
    print(f"Saved files in: {OUTDIR}")






# Builder

def parse_dix_pairs(dix_path: Path) -> list[tuple[str, str]]:
    """
    Parse bilingual pairs from a .dix file.

    Returns:
        list[tuple[str, str]] where each tuple is (left_word, right_word)

    Notes:
    - Only entries with a <p><l>...</l><r>...</r></p> are extracted.
    - Empty pairs are skipped.
    - Duplicate pairs are deduplicated later.
    """
    try:
        tree = ET.parse(dix_path)
    except ET.ParseError as e:
        raise RuntimeError(f"XML parse error in {dix_path}: {e}") from e

    root = tree.getroot()
    pairs = []

    for elem in root.iter():
        if local_name(elem.tag) != "e":
            continue

        # Find direct child <p>
        p = find_first_child(elem, "p")
        if p is None:
            continue

        l = find_first_child(p, "l")
        r = find_first_child(p, "r")
        if l is None or r is None:
            continue

        left = extract_surface_text(l)
        right = extract_surface_text(r)

        if not left or not right:
            continue

        pairs.append((_normalize_text(left), _normalize_text(right)))

    return pairs


def find_single_dix_file(folder: Path) -> Path:
    dix_files = list(folder.glob("*.dix"))
    if not dix_files:
        raise FileNotFoundError(f"No .dix file found in {folder}")
    if len(dix_files) > 1:
        raise RuntimeError(f"More than one .dix file found in {folder}: {[f.name for f in dix_files]}")
    return dix_files[0]


def language_code_from_folder(folder: Path) -> str:
    """
    Uses the folder name as srcLang.
    Expects names like 'ita', 'spa', 'cat', etc.
    """

    name = folder.name.strip()
    pair = name.removeprefix("apertium-")
    srcLang = pair.split("-")[0]
    srcLangCode = get_lang_codes(srcLang)[1]

    if len(srcLangCode) != 3:
        raise ValueError(f"Folder name '{folder.name}' is not a 3-letter language code")
    return srcLangCode


def process_root(root_dir: Path = Path("TranslationDictionariesBuilder/apertium-english-bidix")):
    if not root_dir.is_dir():
        raise NotADirectoryError(f"{root_dir} is not a directory")

    conn, cursor = create_and_define_database()

    processed = 0

    for subfolder in sorted(root_dir.iterdir()):
        if not subfolder.is_dir():
            continue

        src_lang = language_code_from_folder(subfolder)
        dix_path = find_single_dix_file(subfolder)

        pairs = parse_dix_pairs(dix_path)
        oldPairs = read_pairs_from_db(conn, src_lang)

        if oldPairs is not None:
            oldPairs.extend(pairs)
        else:
            oldPairs = pairs

        forward, reverse = build_bidirectional_dicts(oldPairs)

        # Assumption:
        #   <l> is the source-language side
        #   <r> is the English side
        #
        # So:
        #   toEnglish=True  -> language -> English
        #   toEnglish=False -> English -> language
        upsert_dictionary(conn, src_lang, True, forward)
        upsert_dictionary(conn, src_lang, False, reverse)

        conn.commit()
        processed += 1

        print(
            f"[OK] {src_lang}: {dix_path.name} | "
            f"{len(pairs)} pairs | "
            f"{len(forward)} {src_lang}->en keys | "
            f"{len(reverse)} en->{src_lang} keys"
        )

    conn.close()
    print(f"\nDone. Processed {processed} language folders.")


if __name__ == "__main__":
    #download_apertium()
    process_root()

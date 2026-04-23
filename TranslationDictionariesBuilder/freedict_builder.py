from pathlib import Path
import xml.etree.ElementTree as ET

from TranslationDictionariesBuilder.tools import _normalize_text, build_bidirectional_dicts, convert_to_macrolanguage, create_and_define_database, extract_surface_text, find_first_child, local_name, read_pairs_from_db, upsert_dictionary


def parse_dix_pairs(dix_path: Path, toEnglish=True) -> list[tuple[str, str]]:
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
        if local_name(elem.tag) != "entry":
            continue

        # Find direct child <p>
        form = find_first_child(elem, "form")
        if form is None:
            continue

        l = find_first_child(form, "orth")

        sense = find_first_child(elem, "sense")
        if sense is None:
            continue

        cit = find_first_child(sense, "cit", [("type", "trans")])
        if cit is None:
            continue

        r = find_first_child(cit, "quote")
        if r is None:
            continue
        
        if l is None or r is None:
            continue

        left = extract_surface_text(l)
        right = extract_surface_text(r)

        if not left or not right:
            continue
        
        if(toEnglish):
            pairs.append((_normalize_text(left), _normalize_text(right)))
        else:
            pairs.append((_normalize_text(right), _normalize_text(left)))

    return pairs


def language_codes_from_file(file: Path):
    name = file.name.strip()
    name = name.removesuffix(".tei")
    srcLang = name.split("-")[0]
    tgtLang = name.split("-")[1]
    return srcLang, tgtLang


def process_root(root_dir: Path = Path("TranslationDictionariesBuilder/freedict_data")):
    if not root_dir.is_dir():
        raise NotADirectoryError(f"{root_dir} is not a directory")

    conn, cursor = create_and_define_database()

    processed = 0

    for dictFile in sorted(root_dir.iterdir()):
        if not str(dictFile).lower().endswith(".tei"):
            continue

        src_lang, tgt_lang = language_codes_from_file(dictFile)
        
        lang = src_lang
        toEnglish = True
        if(src_lang == "eng"):
            toEnglish = False
            lang = tgt_lang

        macroLang = convert_to_macrolanguage(lang)

        pairs = parse_dix_pairs(dictFile, toEnglish)
        oldPairs = read_pairs_from_db(conn, macroLang)

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
        upsert_dictionary(conn, macroLang, True, forward)
        upsert_dictionary(conn, macroLang, False, reverse)

        conn.commit()
        processed += 1

        print(
            f"[OK] {lang}: {dictFile.name} | "
            f"{len(pairs)} pairs | "
            f"{len(forward)} {lang}->en keys | "
            f"{len(reverse)} en->{lang} keys"
        )

    conn.close()
    print(f"\nDone. Processed {processed} language folders.")



if __name__ == "__main__":
    process_root()
import json
from pathlib import Path

from TranslationDictionariesBuilder.tools import _normalize_text, build_bidirectional_dicts, create_and_define_database, get_all_languages_from_db, get_lang_codes, read_pairs_from_db, upsert_dictionary


def process_root(dict_file: Path = Path("TranslationDictionariesBuilder/wiktionary_data.jsonl")):
    allPairs : dict[str, list[tuple[str, str]]] = {}   #{"lanCode1" : pairs1, "lanCode2" : pairs2, ...}

    conn, cursor = create_and_define_database()

    # we insert all the old data inside allPairs
    languages = get_all_languages_from_db(conn)
    for lang in languages:
        allPairs[lang] = read_pairs_from_db(conn, lang)

    processed = 0

    # we parse all the wiktionary data
    with open(dict_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue  # skip empty lines
            
            wordData = json.loads(line)
            enWord = wordData.get("word")
            if(enWord is None):
                print("Skipped: "+str(enWord))
                continue

            translations = wordData.get("translations")
            if(translations is None): 
                print("Skipped: "+enWord)
                continue

            for translation in translations:
                langCodes = get_lang_codes(translation.get("code"))
                langCode = langCodes[1]

                if(langCode is None or translation.get("word") is None):
                    print("Skipped: "+enWord)
                    continue

                if(langCode in allPairs):
                    allPairs[langCode].append((_normalize_text(translation.get("word")), _normalize_text(enWord)))
                else:
                    allPairs[langCode] = [(_normalize_text(enWord), _normalize_text(translation.get("word")))]
            
    for lang, pairs in allPairs.items():
        #if(len(pairs) < 5000): continue

        forward, reverse = build_bidirectional_dicts(pairs)

        # Assumption:
        #   <l> is the source-language side
        #   <r> is the English side
        #
        # So:
        #   toEnglish=True  -> language -> English
        #   toEnglish=False -> English -> language
        upsert_dictionary(conn, lang, True, forward)
        upsert_dictionary(conn, lang, False, reverse)

        conn.commit()
        processed += 1

        print(
            f"[OK] {lang}| "
            f"{len(pairs)} pairs | "
            f"{len(forward)} {lang}->en keys | "
            f"{len(reverse)} en->{lang} keys"
        )


if __name__ == "__main__":
    process_root()
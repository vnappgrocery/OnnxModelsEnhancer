from collections import defaultdict
from enum import Enum
from pathlib import Path
import re
import shutil
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET

import pycountry
from TranslationDictionariesBuilder.Protobuf import dictionary_data_pb2


def _normalize_text(text: str) -> str:
    if text is None:
        return None

    # Unicode NFC normalization
    text = unicodedata.normalize("NFC", text)

    # Standardize punctuation
    replacements = {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "–": "-",
        "—": "-"
    }
    for k, v in replacements.items():
        text = text.replace(k, v)

    # Trim
    text = text.strip()

    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)

    # Case normalization
    text = text.casefold()

    return text


def convert_to_macrolanguage(lang_code: str):
    """
    Converts a 3-letter dialect code to its corresponding macrolanguage code
    based on standardized mapping. Returns the original code if no mapping exists.
    This conversion is based on dictionaries to run faster.
    """
    
    # Mapping dictionary: Dialect (Key) -> Macrolanguage (Value)
    mapping = {
        # Chinese (zho)
        'cmn': 'zho', 'cjy': 'zho', 'czh': 'zho', 'gan': 'zho', 
        'hsn': 'zho', 'wuu': 'zho', 'hak': 'zho',
        
        # Persian (fas)
        'pes': 'fas', 'prs': 'fas',
        
        # Malay (msa)
        'zsm': 'msa', 'meo': 'msa', 'vkt': 'msa', 'mfa': 'msa',
        
        # Arabic (ara)
        'arb': 'ara', 'arz': 'ara', 'apc': 'ara', 'acm': 'ara', 
        'afb': 'ara', 'ary': 'ara',
        
        # Norwegian (nor)
        'nob': 'nor',
        
        # Serbo-Croatian (hbs)
        'srp': 'hbs', 'hrv': 'hbs', 'bos': 'hbs',
        
        # Kurdish (kur)
        'kmr': 'kur'
    }
    
    # .get() returns the mapped value if found, otherwise returns the original lang_code
    return mapping.get(lang_code.lower(), lang_code)


def create_and_define_database(db_path: str = "TranslationDictionariesBuilder/translation_dict.db"):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """CREATE TABLE IF NOT EXISTS dictionaries(
            srcLang CHAR(3),
            toEnglish BOOLEAN,
            data MEDIUMBLOB,
            PRIMARY KEY (srcLang, toEnglish)
        );"""
    )

    return conn, cursor


def read_pairs_from_db(conn: sqlite3.Connection, src_lang: str, toEnglish = True) -> list[tuple[str, str]]:
    """
    Returns:
        list[tuple[str, str]] where each tuple is (left_word, right_word)
    """
    cur = conn.cursor()
    cur.execute("SELECT data FROM dictionaries WHERE srcLang = ? AND toEnglish = ?", (src_lang, toEnglish))
    rows = cur.fetchall()
    if(len(rows) > 0):
        dictionary = protobuf_bytes_to_dict(rows[0][0])
        pairs = []
        for key, values in dictionary.items():
            for value in values:
                pairs.append((key, value))
        return pairs
    else:
        return None
    

def read_data_from_db(conn: sqlite3.Connection, src_lang: str, toEnglish = True) -> dict[str, list[str]]:
    """
    Returns:
        list[tuple[str, str]] where each tuple is (left_word, right_word)
    """
    cur = conn.cursor()
    cur.execute("SELECT data FROM dictionaries WHERE srcLang = ? AND toEnglish = ?", (src_lang, toEnglish))
    rows = cur.fetchall()
    if(len(rows) > 0):
        dictionary = protobuf_bytes_to_dict(rows[0][0])
        return dictionary
    else:
        return None


def get_all_languages_from_db(conn: sqlite3.Connection) -> list[str]:
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT srcLang FROM dictionaries")
    rows = cur.fetchall()
    return [row[0] for row in rows]


def build_bidirectional_dicts(pairs):
    """
    Build two multi-value dictionaries:
      - forward[left]  -> list of rights
      - reverse[right] -> list of lefts
    """
    forward = defaultdict(set)
    reverse = defaultdict(set)

    for left, right in pairs:
        if(left != right):  #this is to remove the false translations (for example in apertium we have ciao -> ciao for the en->ita dict, that will be used to do the false translation ciao -> ciao also for ita->en)
            if(left not in forward or right not in forward[left]):
                forward[left].add(right)
            if(right not in reverse or left not in reverse[right]):
                reverse[right].add(left)

    forward = {k: v for k, v in forward.items()}
    reverse = {k: v for k, v in reverse.items()}
    return forward, reverse


def dict_to_protobuf_bytes(mapping: dict[str, list[str]]) -> bytes:
    """
    Serialize a Python dictionary[str, list[str]] into protobuf bytes.
    """
    dictionary = dictionary_data_pb2.DataMap()
    for key, values in mapping.items():
        dictionary.data[key].value.extend(values)
    return dictionary.SerializeToString()


def protobuf_bytes_to_dict(blob: bytes) -> dict[str, list[str]]:
    """
    Optional helper to deserialize protobuf bytes back to a Python dict.
    """
    datamap = dictionary_data_pb2.DataMap()
    datamap.ParseFromString(blob)
    dictionary = {}
    for key in datamap.data.keys():
        dictionary[key] = list(datamap.data[key].value)
    return dictionary  #{entry.key: list(entry.values) for entry in dictionary.entries}


def upsert_dictionary(conn: sqlite3.Connection, src_lang: str, to_english: bool, mapping: dict[str, list[str]]):
    blob = dict_to_protobuf_bytes(mapping)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO dictionaries(srcLang, toEnglish, data)
        VALUES (?, ?, ?)
        ON CONFLICT(srcLang, toEnglish)
        DO UPDATE SET data = excluded.data
        """,
        (src_lang, int(to_english), blob),
    )


def local_name(tag: str) -> str:
    """Strip XML namespace if present."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def extract_surface_text(elem: ET.Element) -> str:
    """
    Extract visible lexical text from an Apertium XML node.

    For most bilingual .dix files, <l> and <r> contain word text plus tags like <s/>.
    itertext() gives the textual content and ignores markup, which is what we want.
    """
    if elem is None:
        return ""
    text = "".join(elem.itertext())
    return _normalize_text(text)


def find_first_child(parent: ET.Element, child_name: str, attrs: list[tuple[str, str]] = []):
    for child in parent:
        if local_name(child.tag) == child_name:
            if(attrs is not None and len(attrs)>0):
                allAttrEqual = True
                for attr in attrs:
                    if(child.attrib.get(attr[0]) != attr[1]):
                        allAttrEqual = False
                if(allAttrEqual):
                    return child
            else:
                return child
    return None


def get_lang_codes(code: str) -> tuple[str | None, str | None]:
    if code is None: return None, None

    code = code.strip().lower()

    if len(code) == 2:
        lang = pycountry.languages.get(alpha_2=code)
    elif len(code) == 3:
        lang = pycountry.languages.get(alpha_3=code)
    else:
        return None, None

    if not lang:
        return None, None

    return getattr(lang, "alpha_2", None), getattr(lang, "alpha_3", None)



def count_keys(blob: bytes) -> int:
    data_map = dictionary_data_pb2.DataMap()
    data_map.ParseFromString(blob)
    return len(data_map.data)

def count_words(blob: bytes) -> int:
    data_map = dictionary_data_pb2.DataMap()
    data_map.ParseFromString(blob)
    count = 0
    for value in data_map.data.values():
        count += len(value.value)+1
    return count

def count_text_bytes(blob: bytes) -> int:
    data_map = dictionary_data_pb2.DataMap()
    data_map.ParseFromString(blob)
    count = 0
    for value in data_map.data.values():
        for word in value.value:
            count += len(word.encode("utf-8"))
    for key in data_map.data.keys():
        count += len(key.encode("utf-8"))
    return count


def create_db_redux_copy():
    src = Path("TranslationDictionariesBuilder/translation_dict.db")
    dst = Path("TranslationDictionariesBuilder/translation_dict_redux.db")
    MIN_KEYS = 10000

    if not src.exists():
        raise FileNotFoundError(f"Source database not found: {src}")

    shutil.copy2(src, dst)
    print(f"Copied database: {src} -> {dst}")

    conn = sqlite3.connect(dst)
    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT srcLang, toEnglish, data
            FROM dictionaries
        """)
        rows = cur.fetchall()

        totals_by_lang: dict[str, int] = {}

        for src_lang, to_english, blob in rows:
            key_count = count_keys(blob)
            totals_by_lang[src_lang] = totals_by_lang.get(src_lang, 0) + key_count
            print(f"{src_lang} toEnglish={to_english}: {key_count} keys")

        langs_to_remove = [
            lang for lang, total in totals_by_lang.items()
            if total < MIN_KEYS
        ]

        print("\nTotal keys by language:")
        for lang, total in sorted(totals_by_lang.items()):
            print(f"  {lang}: {total}")

        print("\nLanguages to remove:")
        for lang in langs_to_remove:
            print(f"  {lang}")

        if langs_to_remove:
            cur.executemany(
                "DELETE FROM dictionaries WHERE srcLang = ?",
                [(lang,) for lang in langs_to_remove]
            )
            conn.commit()
            cur.execute("VACUUM")

        print(f"\nRemoved {len(langs_to_remove)} languages from {dst}")

    finally:
        conn.close()

class CountType(Enum):
    KEYS = 0
    WORDS = 1
    TEXT_BYTES = 2

def count_lang_keys(db_path: str = "TranslationDictionariesBuilder/translation_dict_redux.db", count_type: CountType = CountType.KEYS):
    conn = sqlite3.connect(Path(db_path))
    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT srcLang, toEnglish, data
            FROM dictionaries
        """)
        rows = cur.fetchall()

        totals_by_lang: dict[str, int] = {}

        for src_lang, to_english, blob in rows:
            key_count = 0
            if count_type == CountType.KEYS:
                key_count = count_keys(blob)
            elif count_type == CountType.WORDS:
                key_count = count_words(blob)
            elif count_type == CountType.TEXT_BYTES:
                key_count = count_text_bytes(blob)
            totals_by_lang[src_lang] = totals_by_lang.get(src_lang, 0) + key_count

        generalTotal = 0
        print("\nTotal keys by language:")
        for lang, total in sorted(totals_by_lang.items(), key=lambda item: item[1], reverse=True):
            generalTotal += total
            print(f"  {lang}: {total}")
        
        print("\nTotal keys: "+str(generalTotal))

    finally:
        conn.close()


if __name__ == "__main__":
    count_lang_keys(count_type=CountType.TEXT_BYTES)
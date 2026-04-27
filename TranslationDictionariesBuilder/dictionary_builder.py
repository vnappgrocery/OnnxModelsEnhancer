import json
import sqlite3
import time

from TranslationDictionariesBuilder.WordTranslationRanker import WordTranslationRanker
import TranslationDictionariesBuilder.apertium_builder as apertium
import TranslationDictionariesBuilder.freedict_builder as fredict
from TranslationDictionariesBuilder.tools import create_and_define_database, create_db_redux_copy, get_all_languages_from_db, read_data_from_db, upsert_dictionary
import TranslationDictionariesBuilder.wiktionary_builder as wiktionary

def order_dictionary():
    ranker = WordTranslationRanker()
    conn, cursor = create_and_define_database("TranslationDictionariesBuilder/translation_dict_ordered.db")
    conn_old = sqlite3.connect("TranslationDictionariesBuilder/translation_dict_redux.db")
    languages = get_all_languages_from_db(conn_old)
    for lang in languages:
        if(ranker.is_lang_compatible(lang)):
            init_time = time.time()

            data = read_data_from_db(conn, lang, False)
            if(data):
                print("Already ordered: "+lang)
                continue

            print("Ordering "+lang+"...")

            order_language_dict(ranker, conn, conn_old, lang)

            conn.commit()

            print("Ordering of "+lang+" DONE in: "+ str(time.time()-init_time) +" s")
        else:
            print("skipped "+lang)

def order_language_dict(ranker: WordTranslationRanker, conn: sqlite3.Connection, conn_old: sqlite3.Connection, lang: str, fileLog=False):
    data = read_data_from_db(conn_old, lang, False)
    data = ranker.rank_batch(data, target_lang=lang, log=True, fileLogSrcLang=("eng" if fileLog else None))
    upsert_dictionary(conn, lang, to_english=False, mapping=data)

    data = read_data_from_db(conn_old, lang, True)
    data = ranker.rank_batch(data, target_lang="eng", log=True, fileLogSrcLang=(lang if fileLog else None))
    upsert_dictionary(conn, lang, to_english=True, mapping=data)


def print_dict_infos_all(dict_path: str):
    conn = sqlite3.connect(dict_path)
    languages = get_all_languages_from_db(conn)
    for lang in languages:
        print_dict_infos(conn, dict_path, lang)


def print_dict_infos(conn: sqlite3.Connection, dict_path: str, lang:str, file_suffix=""):
    if not conn:
        conn = sqlite3.connect(dict_path)
    data = read_data_from_db(conn, lang, toEnglish=False)
    with open('TranslationDictionariesBuilder/logs/dict_info_eng_'+lang+'_'+file_suffix+'.json', 'w') as f:
        json.dump(data, f)
    data = read_data_from_db(conn, lang, toEnglish=True)
    with open('TranslationDictionariesBuilder/logs/dict_info_'+lang+'_eng_'+file_suffix+'.json', 'w') as f:
        json.dump(data, f)

    

if __name__ == "__main__":
    '''wiktionary.process_root()
    print_dict_infos(None, "TranslationDictionariesBuilder/translation_dict.db", "ita", "wiki")
    #apertium.download_apertium()
    apertium.process_root()
    print_dict_infos(None, "TranslationDictionariesBuilder/translation_dict.db", "ita", "apertium")
    fredict.process_root()
    print_dict_infos(None, "TranslationDictionariesBuilder/translation_dict.db", "ita", "freedict")'''
    create_db_redux_copy()
    order_dictionary()
    '''ranker = WordTranslationRanker()
    conn, cursor = create_and_define_database("TranslationDictionariesBuilder/translation_dict_ordered.db")
    conn_old = sqlite3.connect("TranslationDictionariesBuilder/translation_dict_redux.db")
    order_language_dict(ranker, conn, conn_old, "ita", True)'''
    
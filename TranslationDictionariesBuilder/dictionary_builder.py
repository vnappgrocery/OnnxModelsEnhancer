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
    cursor_old = conn.cursor()
    languages = get_all_languages_from_db(conn_old)
    for lang in languages:
        if(ranker.is_lang_compatible(lang)):
            print("Ordering "+lang+"...")
            init_time = time.time()

            data = read_data_from_db(conn_old, lang, False)
            for word, translations in data.items():
                data[word] = ranker.rank(word, translations, target_lang=lang, log=True)
            upsert_dictionary(conn, lang, to_english=False, mapping=data)

            data = read_data_from_db(conn_old, lang, True)
            for word, translations in data.items():
                data[word] = ranker.rank(word, translations, target_lang="eng")
            upsert_dictionary(conn, lang, to_english=True, mapping=data)

            print("Ordering of "+lang+" DONE in: "+ str(time.time()-init_time) +" s")
        else:
            print("skipped "+lang)



if __name__ == "__main__":
    """wiktionary.process_root()
    #apertium.download_apertium()
    apertium.process_root()
    fredict.process_root()
    create_db_redux_copy()"""
    order_dictionary()
import TranslationDictionariesBuilder.apertium_builder as apertium
import TranslationDictionariesBuilder.freedict_builder as fredict
from TranslationDictionariesBuilder.tools import create_db_redux_copy
import TranslationDictionariesBuilder.wiktionary_builder as wiktionary

if __name__ == "__main__":
    wiktionary.process_root()
    apertium.download_apertium()
    apertium.process_root()
    fredict.process_root()
    create_db_redux_copy()
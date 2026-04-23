from math import isnan
import time
from wordfreq import zipf_frequency, available_languages
from sentence_transformers import SentenceTransformer, util

from TranslationDictionariesBuilder.tools import get_lang_codes

class WordTranslationRanker:
    """supported_laguages = [
        "ar", "bg", "ca", "cs", "da", "de", "el", "en", "es", "et", "fa", "fi", "fr", "fr-ca", "gl", "gu", "he", "hi", "hr", "hu", "hy",
        "id", "it", "ja", "ka", "ko", "ku", "lt", "lv", "mk", "mn", "mr", "ms", "my", "nb", "nl", "pl", "pt", "pt-br", "ro", "ru", "sk", "sl",
        "sq", "sr", "sv", "th", "tr", "uk", "ur", "vi", "zh-cn", "zh-tw", "zh"
]"""
    """supported_laguages = [
        'af', 'sq', 'am', 'ar', 'hy', 'as', 'az', 'eu', 'be', 'bn',
        'bs', 'bg', 'my', 'ca', 'ceb', 'zh', 'co', 'hr', 'cs', 'da',
        'nl', 'en', 'eo', 'et', 'fi', 'fr', 'fy', 'gl', 'ka', 'de',
        'el', 'gu', 'ht', 'ha', 'haw', 'he', 'hi', 'hmn', 'hu', 'is',
        'ig', 'id', 'ga', 'it', 'ja', 'jv', 'kn', 'kk', 'km', 'rw',
        'ko', 'ku', 'ky', 'lo', 'la', 'lv', 'lt', 'lb', 'mk', 'mg',
        'ms', 'ml', 'mt', 'mi', 'mr', 'mn', 'ne', 'no', 'ny', 'or',
        'fa', 'pl', 'pt', 'pa', 'ro', 'ru', 'sm', 'gd', 'sr', 'st',
        'sn', 'si', 'sk', 'sl', 'so', 'es', 'su', 'sw', 'sv', 'tl',
        'tg', 'ta', 'tt', 'te', 'th', 'bo', 'tr', 'tk', 'ug', 'uk',
        'ur', 'uz', 'vi', 'cy', 'wo', 'xh', 'yi', 'yo', 'zu'
]"""


    def __init__(
        self,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        semantic_weight: float = 0.65,
        frequency_weight: float = 0.35,
    ):
        self.models: list[tuple[str, list[str], SentenceTransformer]] = []
        model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
       
        supported_laguages = [
            'ara', 'bul', 'cat', 'ces', 'dan', 'deu', 'ell', 'eng', 'spa', 'est', 'fas', 'fin', 'fra', 'fra-ca', 'glg', 'guj', 'heb', 'hin', 'hrv', 'hun', 'hye',
            'ind', 'ita', 'jpn', 'kat', 'kor', 'kur', 'lit', 'lav', 'mkd', 'mon', 'mar', 'msa', 'mya', 'nor', 'nld', 'pol', 'por', 'por-br', 'ron', 'rus', 'slk', 'slv',
            'sqi', 'srp', 'swe', 'tha', 'tur', 'ukr', 'urd', 'vie', 'zho-cn', 'zho-tw', 'zho', 'hbs'
        ]
        self.models.append((model_name, supported_laguages, SentenceTransformer(model_name)))
        model_name = "setu4993/LEALLA-base"
        
        supported_laguages = [
            'afr', 'sqi', 'amh', 'ara', 'hye', 'asm', 'aze', 'eus', 'bel', 'ben',
            'bos', 'bul', 'mya', 'cat', 'ceb', 'zho', 'cos', 'hrv', 'ces', 'dan',
            'nld', 'eng', 'epo', 'est', 'fin', 'fra', 'fry', 'glg', 'kat', 'deu',
            'ell', 'guj', 'hat', 'hau', 'haw', 'heb', 'hin', 'hmn', 'hun', 'isl',
            'ibo', 'ind', 'gle', 'ita', 'jpn', 'jav', 'kan', 'kaz', 'khm', 'kin',
            'kor', 'kur', 'kir', 'lao', 'lat', 'lav', 'lit', 'ltz', 'mkd', 'mlg',
            'msa', 'mal', 'mlt', 'mri', 'mar', 'mon', 'nep', 'nor', 'nya', 'ori',
            'fas', 'pol', 'por', 'pan', 'ron', 'rus', 'smo', 'gla', 'srp', 'sot',
            'sna', 'sin', 'slk', 'slv', 'som', 'spa', 'sun', 'swa', 'swe', 'tgl',
            'tgk', 'tam', 'tat', 'tel', 'tha', 'bod', 'tur', 'tuk', 'uig', 'ukr',
            'urd', 'uzb', 'vie', 'cym', 'wol', 'xho', 'yid', 'yor', 'zul', 'hbs'
        ]
        self.models.append((model_name, supported_laguages, SentenceTransformer(model_name)))
        self.semantic_weight = semantic_weight
        self.frequency_weight = frequency_weight

    @staticmethod
    def _normalize_frequency(word: str, lang: str) -> float:
        """
        wordfreq returns Zipf frequency, roughly on a 0-8 scale.
        We map that to 0-1.
        """
        lang_code = get_lang_codes(lang)
        if(not lang_code or not lang_code[0]): return False
        available_freq_languages = available_languages(wordlist="small").keys()
        if lang_code[0] not in available_freq_languages and lang_code[1] not in available_freq_languages:
            return 0
        z = zipf_frequency(word, lang_code[0])
        if isnan(z):
            z = 0.0
        return max(0.0, min(1.0, z / 8.0))

    def rank(self, source_word: str, candidates: list[str], target_lang: str, log=False) -> list[str]:
        if not candidates:
            return []
        
        init_time = time.time()
        
        tgt_lang_code = get_lang_codes(target_lang)

        if(not tgt_lang_code or not tgt_lang_code[0]): return []

        semantic_scores = [0] * len(candidates)  #array containing only zeros, with the length of candidates

        for model_name, supported_langs, model in self.models:
            if(tgt_lang_code[1] in supported_langs):
                source_emb = model.encode(source_word, convert_to_tensor=True)
                cand_embs = model.encode(candidates, convert_to_tensor=True)
                semantic_scores = util.cos_sim(source_emb, cand_embs)[0].tolist()

        results = []
        for candidate, sem in zip(candidates, semantic_scores):
            freq = self._normalize_frequency(candidate, tgt_lang_code[0])
            final = self.semantic_weight * float(sem) + self.frequency_weight * freq
            results.append({
                "translation": candidate,
                "semantic_score": float(sem),
                "frequency_score": float(freq),
                "final_score": float(final),
            })

        if all(v["final_score"] == 0 for v in results):
            return []

        results.sort(key=lambda x: x["final_score"], reverse=True)
        resultTexts = []
        for result in results:
            resultTexts.append(result["translation"])

        if(log): print("Word "+source_word+"reordered in "+str(time.time()-init_time)+" s")
        return resultTexts
    

    def is_lang_compatible(self, lang: str) -> bool:
        lang_code = get_lang_codes(lang)
        if(not lang_code or not lang_code[0]): return False
        available_freq_languages = available_languages(wordlist="small").keys()
        if lang_code[0] in available_freq_languages or lang_code[1] in available_freq_languages:
            return True
        for model_name, supported_langs, model in self.models:
            if(lang_code[1] in supported_langs):
                return True
        return False
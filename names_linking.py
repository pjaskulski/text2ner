import json
import os
import re
import shutil
import fcntl
import threading
import time
from contextvars import ContextVar
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup, NavigableString
from dotenv import load_dotenv
from google import genai
from google.genai import types


load_dotenv()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
SUPPORTED_GEMINI_MODELS = {
    "gemini-3.1-flash-lite-preview": "Gemini 3.1 Flash Lite Preview",
    "gemini-3-flash-preview": "Gemini 3 Flash Preview",
}
PREFERRED_WIKIBASE_LANGUAGES = ("pl", "la", "en", "de", "hu", "cs")
TIMEOUT_MS = 120 * 1000
MAX_REASONABLE_LIFESPAN_YEARS = 100
POSTHUMOUS_CONTEXT_GRACE_YEARS = 25
ENABLE_WIKIDATA_SEMANTIC_FALLBACK = False
WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
PLWIKI_API_URL = "https://pl.wikipedia.org/w/api.php"
SUPPORTED_TEI_TAG_TYPES = ("persName", "placeName", "date", "roleName", "orgName")
DEFAULT_ENABLED_TAG_TYPES = ("persName", "placeName", "date", "roleName")

client = genai.Client(api_key=GEMINI_API_KEY)
CURRENT_DIAGNOSTIC_LOG_PATH = ContextVar("CURRENT_DIAGNOSTIC_LOG_PATH", default=None)
CURRENT_GEMINI_MODEL = ContextVar("CURRENT_GEMINI_MODEL", default=DEFAULT_GEMINI_MODEL)
DIAGNOSTIC_LOG_RETENTION_HOURS = 48
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
WIKIDATA_REQUEST_INTERVAL_SECONDS = float(os.environ.get("WIKIDATA_REQUEST_INTERVAL_SECONDS", "0.75"))
WIKIDATA_429_MAX_RETRIES = int(os.environ.get("WIKIDATA_429_MAX_RETRIES", "2"))
WIKIDATA_429_DEFAULT_WAIT_SECONDS = int(os.environ.get("WIKIDATA_429_DEFAULT_WAIT_SECONDS", "60"))
WIKIDATA_429_MAX_WAIT_SECONDS = int(os.environ.get("WIKIDATA_429_MAX_WAIT_SECONDS", "120"))
WIKIDATA_THROTTLE_STATE_PATH = os.environ.get(
    "WIKIDATA_THROTTLE_STATE_PATH",
    "/tmp/text2ner_wikidata_throttle.state",
)
_WIKIDATA_THROTTLE_LOCK = threading.Lock()
_LAST_WIKIDATA_REQUEST_AT = 0.0


WIKIBASE_SOURCES = {
    "WikiHum": {
        "source": "WikiHum",
        "api_url": "https://wikihum.lab.dariah.pl/api.php",
        "entity_base_url": "https://wikihum.lab.dariah.pl/entity",
        "instance_of_property": "P27",
        "person_type_ids": {"Q5"},
        "place_type_ids": {"Q48", "Q50", "Q4"},
    },
    "va.wiki.kul.pl": {
        "source": "va.wiki.kul.pl",
        "api_url": "https://va.wiki.kul.pl/w/api.php",
        "entity_base_url": "https://va.wiki.kul.pl/entity",
        "instance_of_property": "P1",
        "person_type_ids": {"Q64"},
        "place_type_ids": {"Q68", "Q2342", "Q81"},
        "priority_claim_property_ids": ("P25", "P63", "P37"),
    },
    "Wikidata": {
        "source": "Wikidata",
        "api_url": "https://www.wikidata.org/w/api.php",
        "entity_base_url": "https://www.wikidata.org/entity",
        "instance_of_property": "P31",
        "person_type_ids": {"Q5"},
        "place_type_ids": {"Q515", "Q532", "Q486972", "Q6256", "Q82794", "Q1620908"},
        "priority_claim_property_ids": ("P39", "P106"),
    },
}

ENTITY_CACHE = {}
WIKIDATA_SITELINK_CACHE = {}
WIKIPEDIA_LEAD_CACHE = {}
PLWIKI_PAGE_CACHE = {}

DATE_MONTH_TOKENS = {
    "ian": 1,
    "ianu": 1,
    "ianuarii": 1,
    "ianuarius": 1,
    "janu": 1,
    "january": 1,
    "januarii": 1,
    "januarius": 1,
    "januar": 1,
    "januari": 1,
    "stycznia": 1,
    "styczeń": 1,
    "feb": 2,
    "febr": 2,
    "februarii": 2,
    "februarius": 2,
    "february": 2,
    "februar": 2,
    "lutego": 2,
    "luty": 2,
    "martii": 3,
    "martius": 3,
    "mart": 3,
    "march": 3,
    "marzec": 3,
    "marca": 3,
    "marzec": 3,
    "april": 4,
    "apr": 4,
    "aprilis": 4,
    "aprili": 4,
    "kwietnia": 4,
    "kwiecień": 4,
    "maii": 5,
    "maius": 5,
    "magii": 5,
    "maja": 5,
    "may": 5,
    "mai": 5,
    "maj": 5,
    "iunii": 6,
    "iunius": 6,
    "iun": 6,
    "junii": 6,
    "junius": 6,
    "jun": 6,
    "june": 6,
    "czerwca": 6,
    "czerwiec": 6,
    "iulii": 7,
    "iulius": 7,
    "iul": 7,
    "julii": 7,
    "julius": 7,
    "jul": 7,
    "july": 7,
    "lipca": 7,
    "lipiec": 7,
    "augusti": 8,
    "augustus": 8,
    "aug": 8,
    "august": 8,
    "sierpnia": 8,
    "sierpień": 8,
    "sept": 9,
    "septemb": 9,
    "septem": 9,
    "septembris": 9,
    "september": 9,
    "septembra": 9,
    "wrzesnia": 9,
    "września": 9,
    "wrzesień": 9,
    "oct": 10,
    "octob": 10,
    "octobris": 10,
    "october": 10,
    "oktober": 10,
    "pazdziernika": 10,
    "października": 10,
    "październik": 10,
    "nov": 11,
    "novemb": 11,
    "novembris": 11,
    "november": 11,
    "listopada": 11,
    "listopad": 11,
    "dec": 12,
    "decemb": 12,
    "decembr": 12,
    "decembris": 12,
    "december": 12,
    "grudnia": 12,
    "grudzień": 12
}

ORG_NAME_SIGNAL_STEMS = (
    "ecclesi",
    "sedes",
    "apostolic",
    "curi",
    "capitul",
    "ordo",
    "ordinis",
    "camera",
    "cancellari",
    "congregat",
)

HUMAN_TYPE_MARKERS = {
    "human", "człowiek", "czlowiek", "person", "osoba", "persona", "czlowiek"
}

NON_PERSON_CONCEPT_MARKERS = {
    "affinal",
    "ancestor",
    "aunt",
    "babcia",
    "cousin",
    "descendant",
    "dziadek",
    "family relationship",
    "grandfather",
    "grandmother",
    "kinship",
    "kinship term",
    "mother's sibling",
    "nephew",
    "niece",
    "parent",
    "pokrewieństwo",
    "relative",
    "relationship",
    "relation",
    "rodzaj pokrewieństwa",
    "sibling",
    "uncle",
    "wuj",
}

PLACE_TYPE_MARKERS = {
    "administrative unit",
    "administrative territorial entity",
    "city",
    "commune",
    "country",
    "county",
    "district",
    "federal state",
    "historical region",
    "human settlement",
    "kingdom",
    "land",
    "municipality",
    "place",
    "province",
    "region",
    "settlement",
    "state",
    "territory",
    "town",
    "village",
    "voivodeship",
    "ziemia",
    "województwo",
    "wojewodztwo",
    "powiat",
    "gmina",
    "miejscowość",
    "miejscowosc",
    "miasto",
    "osada",
    "wieś",
    "wies",
    "kraj",
    "królestwo",
    "krolestwo",
    "państwo",
    "panstwo",
    "region historyczny",
    "kraina",
    "prowincja",
    "palatinatus",
}

GENERIC_PLACE_SIGNAL_TOKENS = {
    "civitas", "civitate", "civitatem", "diocesis", "diocese", "diocesi",
    "locus", "locum", "terra", "regio", "partes", "urbs", "oppidum",
}

DEFAULT_POLISH_PERSON_EQUIVALENTS = {
    "andreas": "Andrzej",
    "casimirus": "Kazimierz",
    "fredericus": "Fryderyk",
    "fridericus": "Fryderyk",
    "henricus": "Henryk",
    "ioannes": "Jan",
    "iohannes": "Jan",
    "iohannis": "Jan",
    "ioannis": "Jan",
    "jacobus": "Jakub",
    "johannes": "Jan",
    "ladislaus": "Władysław",
    "nicolaus": "Mikołaj",
    "paulus": "Paweł",
    "petrus": "Piotr",
    "stanislaus": "Stanisław",
    "vladislaus": "Władysław",
    "wenceslaus": "Wacław",
}

PERSON_SEARCH_VARIANTS = {
    "augustino": ["Augustinus", "Augustyn", "Agostino", "Augustine"],
    "augustinus": ["Augustyn", "Agostino", "Augustine"],
    "augustyn": ["Augustinus", "Agostino", "Augustine"],
    "ioannes": ["Johannes", "Johann", "Jan"],
    "iohannes": ["Johannes", "Johann", "Jan"],
    "ioannis": ["Johannes", "Johann", "Jan"],
    "iohannis": ["Johannes", "Johann", "Jan"],
    "johannes": ["Johannes", "Johann", "Jan"],
}

DEFAULT_POLISH_PLACE_EQUIVALENTS = {
    "chelmno": "Chełmno",
    "cracovia": "Kraków",
    "culm": "Chełmno",
    "culmensibus": "Chełmno",
    "culmensis": "Chełmno",
    "gedanum": "Gdańsk",
    "gedanensis": "Gdańsk",
    "lucca": "Lukka",
    "luca": "Lukka",
    "missen": "Miśnia",
    "missini": "Miśnia",
    "misnia": "Miśnia",
    "misnensis": "Miśnia",
    "polonia": "Polska",
    "posnania": "Poznań",
    "posnaniensis": "Poznań",
    "perugia": "Perugia",
    "perusia": "Perugia",
    "perusina": "Perugia",
    "perusino": "Perugia",
    "perusinus": "Perugia",
    "prussia": "Prusy",
    "russia": "Ruś",
    "ruthenia": "Ruś",
    "vesprimiensis": "Veszprém",
    "thorun": "Toruń",
    "torunia": "Toruń",
}

PLACE_SEARCH_VARIANTS = {
    "missen": ["Meißen", "Meissen", "Miśnia"],
    "missini": ["Meißen", "Meissen", "Miśnia"],
    "misnia": ["Meißen", "Meissen", "Miśnia"],
    "misnensis": ["Meißen", "Meissen", "Miśnia"],
    "vesprimiensis": ["Veszprém", "Veszprem"],
}

DEFAULT_POLISH_PLACE_ADJECTIVAL_EQUIVALENTS = {
    "chelmno": "chełmiński",
    "culm": "chełmiński",
    "culmensis": "chełmiński",
    "culmensi": "chełmiński",
    "culmensibus": "chełmiński",
    "cracovia": "krakowski",
    "cracoviensis": "krakowski",
    "gedanensis": "gdański",
    "gnesnensis": "gnieźnieński",
    "gnesnensem": "gnieźnieński",
    "lucca": "lukeński",
    "luca": "lukeński",
    "missen": "miśnieński",
    "missini": "miśnieński",
    "misnia": "miśnieński",
    "misnensis": "miśnieński",
    "pomerania": "pomorski",
    "pomeranie": "pomorski",
    "posnania": "poznański",
    "posnaniensis": "poznański",
    "perugia": "perugiański",
    "perusia": "perugiański",
    "perusina": "perugiański",
    "perusino": "perugiański",
    "perusinus": "perugiański",
    "prussia": "pruski",
    "russia": "ruski",
    "ruthenia": "ruski",
    "vesprimiensis": "veszprémski",
    "thorun": "toruński",
    "torunia": "toruński",
}

DEFAULT_PLWIKI_OFFICE_EQUIVALENTS = {
    "archiepiscopus": "arcybiskup",
    "bishop": "biskup",
    "biskup": "biskup",
    "bp": "biskup",
    "canon": "kanonik",
    "canonicus": "kanonik",
    "cardinal": "kardynał",
    "cardinalis": "kardynał",
    "chancellor": "kanclerz",
    "cancellarius": "kanclerz",
    "collector": "poborca",
    "collectoris": "poborca",
    "dux": "książę",
    "episcopus": "biskup",
    "hetman": "hetman",
    "kanclerz": "kanclerz",
    "kanonik": "kanonik",
    "kapłan": "kapłan",
    "kaplan": "kapłan",
    "kardynal": "kardynał",
    "kardynał": "kardynał",
    "king": "król",
    "król": "król",
    "krol": "król",
    "książę": "książę",
    "ksiaze": "książę",
    "notarius": "notariusz",
    "notary": "notariusz",
    "opat": "opat",
    "papa": "papież",
    "papiez": "papież",
    "papież": "papież",
    "pape": "papież",
    "papal": "papieski",
    "pope": "papież",
    "presbyter": "prezbiter",
    "priest": "kapłan",
    "rex": "król",
    "sacerdos": "kapłan",
    "thesaurarius": "skarbnik",
    "treasurer": "skarbnik",
    "vir": "duchowny",
    "wojewoda": "wojewoda",
}


def _normalize_config_key(value):
    """Porządkuje klucz z pliku konfiguracyjnego do postaci używanej przy lookupach."""
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _normalize_config_value(value):
    """Porządkuje wartość tekstową z pliku konfiguracyjnego."""
    return re.sub(r"\s+", " ", str(value or "").strip())


def load_json_mapping_config(filename, default_mapping):
    """Wczytuje słownik tekstowy z pliku JSON i bezpiecznie wraca do danych domyślnych przy błędzie."""
    config_path = os.path.join(CONFIG_DIR, filename)
    normalized_default = {
        _normalize_config_key(key): _normalize_config_value(value)
        for key, value in default_mapping.items()
        if _normalize_config_key(key) and _normalize_config_value(value)
    }

    if not os.path.exists(config_path):
        print(
            f"[TEXT2NER-CONFIG] Brak pliku {config_path}; "
            f"używam wartości domyślnych."
        )
        return dict(normalized_default)

    try:
        with open(config_path, "r", encoding="utf-8") as config_file:
            raw_mapping = json.load(config_file)
    except Exception as exc:
        print(
            f"[TEXT2NER-CONFIG] Nie udało się wczytać {config_path}: {exc}; "
            f"używam wartości domyślnych."
        )
        return dict(normalized_default)

    if not isinstance(raw_mapping, dict):
        print(
            f"[TEXT2NER-CONFIG] Plik {config_path} nie zawiera obiektu JSON; "
            f"używam wartości domyślnych."
        )
        return dict(normalized_default)

    normalized_mapping = {}
    for raw_key, raw_value in raw_mapping.items():
        key = _normalize_config_key(raw_key)
        value = _normalize_config_value(raw_value)
        if not key or not value:
            continue
        normalized_mapping[key] = value

    if not normalized_mapping:
        print(
            f"[TEXT2NER-CONFIG] Plik {config_path} nie zawiera poprawnych par tekstowych; "
            f"używam wartości domyślnych."
        )
        return dict(normalized_default)

    return normalized_mapping


def get_editable_dictionary_definitions():
    """Zwraca metadane słowników konfiguracyjnych edytowalnych z poziomu aplikacji."""
    definitions = []
    for config_key, config in EDITABLE_DICTIONARY_CONFIGS.items():
        current_mapping = globals()[config["global_name"]]
        definitions.append({
            "key": config_key,
            "filename": config["filename"],
            "title": config["title"],
            "description": config["description"],
            "entry_count": len(current_mapping),
        })
    return definitions


def get_editable_dictionary_snapshot():
    """Zwraca bieżący stan wszystkich edytowalnych słowników w postaci gotowej dla UI."""
    snapshot = {}
    for config_key, config in EDITABLE_DICTIONARY_CONFIGS.items():
        current_mapping = globals()[config["global_name"]]
        snapshot[config_key] = [
            {"key": key, "value": value}
            for key, value in sorted(current_mapping.items())
        ]
    return snapshot


def validate_editable_dictionary_entries(entries):
    """Waliduje i normalizuje listę wpisów słownika przesłaną z interfejsu."""
    if not isinstance(entries, list):
        raise ValueError("Słownik musi być przekazany jako lista wierszy.")

    normalized_mapping = {}
    original_keys = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Każdy wiersz słownika musi być obiektem JSON.")

        raw_key = entry.get("key", "")
        raw_value = entry.get("value", "")
        normalized_key = _normalize_config_key(raw_key)
        normalized_value = _normalize_config_value(raw_value)

        if not normalized_key and not normalized_value:
            continue
        if not normalized_key or not normalized_value:
            raise ValueError("Każdy wiersz słownika musi zawierać niepusty klucz i wartość.")
        if normalized_key in normalized_mapping:
            duplicate_key = original_keys[normalized_key]
            raise ValueError(f"Powielony klucz słownika: '{duplicate_key}'.")

        normalized_mapping[normalized_key] = normalized_value
        original_keys[normalized_key] = normalized_key

    if not normalized_mapping:
        raise ValueError("Słownik nie może być pusty.")

    return dict(sorted(normalized_mapping.items()))


def write_editable_dictionary_config(config_key, normalized_mapping):
    """Zapisuje wskazany słownik konfiguracyjny do pliku JSON i odświeża go w pamięci."""
    config = EDITABLE_DICTIONARY_CONFIGS.get(config_key)
    if not config:
        raise ValueError("Nieznany słownik konfiguracyjny.")
    if not isinstance(normalized_mapping, dict) or not normalized_mapping:
        raise ValueError("Brak poprawnych danych słownika do zapisu.")

    os.makedirs(CONFIG_DIR, exist_ok=True)
    config_path = os.path.join(CONFIG_DIR, config["filename"])
    backup_path = config_path + ".bak"

    if os.path.exists(config_path):
        shutil.copy2(config_path, backup_path)

    with open(config_path, "w", encoding="utf-8") as config_file:
        json.dump(normalized_mapping, config_file, ensure_ascii=False, indent=2, sort_keys=True)
        config_file.write("\n")

    globals()[config["global_name"]] = load_json_mapping_config(
        config["filename"],
        config["default_mapping"],
    )


POLISH_PERSON_EQUIVALENTS = load_json_mapping_config(
    "person_equivalents.json",
    DEFAULT_POLISH_PERSON_EQUIVALENTS,
)
POLISH_PLACE_EQUIVALENTS = load_json_mapping_config(
    "place_equivalents.json",
    DEFAULT_POLISH_PLACE_EQUIVALENTS,
)
POLISH_PLACE_ADJECTIVAL_EQUIVALENTS = load_json_mapping_config(
    "place_adjectival_equivalents.json",
    DEFAULT_POLISH_PLACE_ADJECTIVAL_EQUIVALENTS,
)
PLWIKI_OFFICE_EQUIVALENTS = load_json_mapping_config(
    "plwiki_office_equivalents.json",
    DEFAULT_PLWIKI_OFFICE_EQUIVALENTS,
)

EDITABLE_DICTIONARY_CONFIGS = {
    "person_equivalents": {
        "filename": "person_equivalents.json",
        "title": "Osoby",
        "description": "Historyczne i łacińskie formy imion z polskimi odpowiednikami.",
        "global_name": "POLISH_PERSON_EQUIVALENTS",
        "default_mapping": DEFAULT_POLISH_PERSON_EQUIVALENTS,
    },
    "place_equivalents": {
        "filename": "place_equivalents.json",
        "title": "Miejsca",
        "description": "Historyczne i łacińskie nazwy miejsc z polskimi odpowiednikami.",
        "global_name": "POLISH_PLACE_EQUIVALENTS",
        "default_mapping": DEFAULT_POLISH_PLACE_EQUIVALENTS,
    },
    "place_adjectival_equivalents": {
        "filename": "place_adjectival_equivalents.json",
        "title": "Przymiotniki miejscowe",
        "description": "Nazwy miejsc mapowane na polskie formy przymiotnikowe.",
        "global_name": "POLISH_PLACE_ADJECTIVAL_EQUIVALENTS",
        "default_mapping": DEFAULT_POLISH_PLACE_ADJECTIVAL_EQUIVALENTS,
    },
    "plwiki_office_equivalents": {
        "filename": "plwiki_office_equivalents.json",
        "title": "Urzędy i funkcje",
        "description": "Formy urzędów i ról używane przy dodatkowym wyszukiwaniu w polskiej Wikipedii.",
        "global_name": "PLWIKI_OFFICE_EQUIVALENTS",
        "default_mapping": DEFAULT_PLWIKI_OFFICE_EQUIVALENTS,
    },
}

TAG_PROMPT_LABELS = {
    "persName": "<persName> dla osób",
    "placeName": "<placeName> dla miejsc, regionów, krajów, miast i jednostek terytorialnych",
    "orgName": "<orgName> dla instytucji, organizacji, wspólnot i ciał kościelnych lub politycznych",
    "roleName": "<roleName> dla urzędów, funkcji, godności i określeń roli społecznej lub kościelnej",
    "date": "<date> dla dat",
}

SEMANTIC_FALLBACK_OFFICE_FAMILIES = {
    "bishop_family": {
        "entity_ids": ["Q611644"],
        "triggers": [
            "episcopus", "bishop", "biskup", "katolicki biskup",
            "catholic bishop", "roman catholic bishop"
        ],
        "keywords": [
            "episcopus", "bishop", "biskup", "catholic bishop",
            "roman catholic bishop", "katolicki biskup", "bishop of"
        ],
    },
    "cardinal_family": {
        "entity_ids": [],
        "triggers": ["cardinalis", "cardinal", "kardynał", "kardynal"],
        "keywords": ["cardinalis", "cardinal", "kardynał", "kardynal"],
    },
    "priest_family": {
        "entity_ids": [],
        "triggers": ["presbyter", "priest", "kapłan", "kaplan", "sacerdos", "canon", "kanonik"],
        "keywords": ["presbyter", "priest", "kapłan", "kaplan", "sacerdos", "canon", "kanonik", "cleric", "duchowny"],
    },
    "papal_family": {
        "entity_ids": [],
        "triggers": ["papa", "pontifex", "papież", "papiez", "pope"],
        "keywords": ["papa", "pontifex", "papież", "papiez", "pope"],
    },
    "ruler_family": {
        "entity_ids": [],
        "triggers": ["rex", "król", "krol", "regina", "królowa", "krolowa", "dux", "książę", "ksiaze"],
        "keywords": ["rex", "król", "krol", "regina", "królowa", "krolowa", "dux", "książę", "ksiaze", "king", "queen", "duke"],
    },
    "official_family": {
        "entity_ids": [],
        "triggers": ["collector", "notarius", "notary", "wojewoda", "palatinus", "cancellarius", "kanclerz"],
        "keywords": ["collector", "collectoris", "notarius", "notary", "wojewoda", "palatinus", "cancellarius", "kanclerz", "official"],
    },
}


# -------------------------------- HELPERS ------------------------------------
def is_wikidata_url(url):
    """Sprawdza, czy żądanie trafia do publicznych usług Wikidaty."""
    return "wikidata.org" in normalize_whitespace(url).lower()


def throttle_wikidata_request():
    """Ogranicza tempo zapytań do Wikidaty także między workerami aplikacji."""
    if WIKIDATA_REQUEST_INTERVAL_SECONDS <= 0:
        return

    try:
        with open(WIKIDATA_THROTTLE_STATE_PATH, "a+", encoding="utf-8") as throttle_file:
            fcntl.flock(throttle_file, fcntl.LOCK_EX)
            throttle_file.seek(0)
            raw_timestamp = throttle_file.read().strip()
            last_request_at = float(raw_timestamp) if raw_timestamp else 0.0
            now = time.monotonic()
            wait_seconds = WIKIDATA_REQUEST_INTERVAL_SECONDS - (now - last_request_at)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            throttle_file.seek(0)
            throttle_file.truncate()
            throttle_file.write(str(time.monotonic()))
            throttle_file.flush()
            fcntl.flock(throttle_file, fcntl.LOCK_UN)
            return
    except Exception:
        pass

    throttle_wikidata_request_in_process()


def throttle_wikidata_request_in_process():
    """Awaryjny limiter używany, gdy nie uda się skorzystać z blokady plikowej."""
    global _LAST_WIKIDATA_REQUEST_AT
    with _WIKIDATA_THROTTLE_LOCK:
        now = time.monotonic()
        wait_seconds = WIKIDATA_REQUEST_INTERVAL_SECONDS - (now - _LAST_WIKIDATA_REQUEST_AT)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _LAST_WIKIDATA_REQUEST_AT = time.monotonic()


def parse_retry_after_seconds(value):
    """Interpretuje nagłówek Retry-After jako liczbę sekund oczekiwania."""
    value = normalize_whitespace(value)
    if not value:
        return WIKIDATA_429_DEFAULT_WAIT_SECONDS
    if value.isdigit():
        return int(value)
    return WIKIDATA_429_DEFAULT_WAIT_SECONDS


def get_json_response(url, params, headers, source_label):
    """Pobiera JSON z defensywną obsługą błędów HTTP i odpowiedzi nie-JSON."""
    wikidata_request = is_wikidata_url(url)
    max_attempts = WIKIDATA_429_MAX_RETRIES + 1 if wikidata_request else 1
    response = None

    for attempt in range(1, max_attempts + 1):
        if wikidata_request:
            throttle_wikidata_request()

        response = requests.get(url, params=params, headers=headers, timeout=30)
        if response.status_code != 429 or not wikidata_request or attempt >= max_attempts:
            break

        retry_after = parse_retry_after_seconds(response.headers.get("Retry-After"))
        retry_after = max(1, min(retry_after, WIKIDATA_429_MAX_WAIT_SECONDS))
        diagnostic_log(
            f"Wikidata zwróciła HTTP 429 dla {source_label}; "
            f"czekam {retry_after}s przed ponowieniem ({attempt}/{max_attempts - 1})."
        )
        time.sleep(retry_after)

    content_type = response.headers.get("Content-Type", "")
    if response.status_code >= 400:
        preview = response.text[:200].replace("\n", " ")
        raise ValueError(
            f"HTTP {response.status_code} dla {source_label}; "
            f"content-type={content_type}; body={preview}"
        )
    if "json" not in content_type.lower():
        preview = response.text[:200].replace("\n", " ")
        raise ValueError(
            f"Odpowiedź nie jest JSON dla {source_label}; "
            f"content-type={content_type}; body={preview}"
        )
    return response.json()


def normalize_whitespace(value):
    """Normalizuje odstępy i bezpiecznie zamienia pustą wartość na napis."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_for_lookup(value):
    """Upraszcza tekst do porównań i wyszukiwania w indeksach tekstowych."""
    value = normalize_whitespace(value).casefold()
    value = re.sub(r"[^\w\s-]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def tokenize_for_match(value):
    """Dzieli tekst na krótszą listę tokenów używaną przy prostym scoringu."""
    cleaned = re.sub(r"[^\w\s-]", " ", normalize_whitespace(value).casefold(), flags=re.UNICODE)
    return [token for token in cleaned.split() if len(token) > 2]


def has_posthumous_context(context, context_clues=None):
    """Ocena, czy kontekst wskazuje, że dokument mówi o osobie już zmarłej."""
    clue_text = " ".join(normalize_whitespace(value) for value in context_clues or []).casefold()
    context_text = normalize_for_lookup(context)
    markers = {
        "bone memorie",
        "bonae memorie",
        "bonae memoriae",
        "felicis recordacionis",
        "felicis recordationis",
        "recordacionis",
        "recordationis",
        "quondam",
        "olim",
        "osoba zmarła",
        "osoba zmarla",
        "zmarła",
        "zmarla",
        "zmarły",
        "zmarly",
        "spadku po nim",
        "spadku po niej",
        "posthum",
    }
    return any(marker in clue_text or marker in context_text for marker in markers)


def build_casefold_lookup(value):
    """Tworzy najprostszą wersję tekstu do porównań case-insensitive."""
    return normalize_whitespace(value).casefold()


def escape_sparql_string_literal(value):
    """Escapuje tekst tak, aby można go było bezpiecznie wstawić do SPARQL."""
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def purge_expired_diagnostic_logs(log_dir, retention_hours=DIAGNOSTIC_LOG_RETENTION_HOURS):
    """Usuwa pliki `.log` starsze niż zadany próg retencji i zwraca podsumowanie operacji."""
    cutoff = datetime.now() - timedelta(hours=retention_hours)
    removed_files = []
    failed_files = []

    for entry in os.scandir(log_dir):
        if not entry.is_file() or not entry.name.endswith(".log"):
            continue
        try:
            modified_at = datetime.fromtimestamp(entry.stat().st_mtime)
        except OSError:
            failed_files.append(entry.name)
            continue
        if modified_at >= cutoff:
            continue
        try:
            os.remove(entry.path)
            removed_files.append(entry.name)
        except OSError:
            failed_files.append(entry.name)

    return {
        "removed_files": removed_files,
        "failed_files": failed_files,
        "retention_hours": retention_hours,
    }


def start_diagnostic_session(log_dir="log"):
    """Tworzy plik logu dla pojedynczego uruchomienia analizy i ustawia go jako aktywny."""
    os.makedirs(log_dir, exist_ok=True)
    cleanup_summary = purge_expired_diagnostic_logs(log_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"{timestamp}.log")
    counter = 1
    while os.path.exists(log_path):
        log_path = os.path.join(log_dir, f"{timestamp}_{counter:02d}.log")
        counter += 1

    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(
            f"[TEXT2NER-DIAG] Start analizy: {datetime.now().isoformat(timespec='seconds')}\n"
        )
    CURRENT_DIAGNOSTIC_LOG_PATH.set(log_path)
    removed_count = len(cleanup_summary["removed_files"])
    failed_count = len(cleanup_summary["failed_files"])
    if removed_count:
        diagnostic_log(
            f"Usunięto {removed_count} plik(ów) logu starszych niż "
            f"{cleanup_summary['retention_hours']} godzin."
        )
    if failed_count:
        diagnostic_log(
            f"Nie udało się usunąć {failed_count} przeterminowanych plików logu: "
            f"{cleanup_summary['failed_files']}"
        )
    return log_path


def stop_diagnostic_session():
    """Czyści informację o aktywnym pliku logu dla bieżącego kontekstu."""
    CURRENT_DIAGNOSTIC_LOG_PATH.set(None)


def diagnostic_log(message):
    """Zapisuje komunikat diagnostyczny do pliku sesji albo na stdout."""
    line = f"[TEXT2NER-DIAG] {message}"
    log_path = CURRENT_DIAGNOSTIC_LOG_PATH.get()
    if log_path:
        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(f"{line}\n")
    else:
        print(line)


def normalize_enabled_tag_types(tag_types):
    """Normalizuje listę włączonych tagów do bezpiecznego, uporządkowanego zestawu."""
    if tag_types is None:
        return list(DEFAULT_ENABLED_TAG_TYPES)

    if isinstance(tag_types, str):
        tag_types = [tag_types]

    normalized = []
    seen = set()
    for tag_type in tag_types or []:
        normalized_tag = normalize_whitespace(tag_type)
        if normalized_tag not in SUPPORTED_TEI_TAG_TYPES or normalized_tag in seen:
            continue
        seen.add(normalized_tag)
        normalized.append(normalized_tag)
    return normalized


def normalize_gemini_model_name(model_name):
    """Zwraca wspieraną nazwę modelu Gemini albo domyślny model aplikacji."""
    normalized_name = normalize_whitespace(model_name)
    if normalized_name in SUPPORTED_GEMINI_MODELS:
        return normalized_name
    return DEFAULT_GEMINI_MODEL


def set_current_gemini_model(model_name):
    """Ustawia model Gemini używany w bieżącym kontekście żądania."""
    normalized_model = normalize_gemini_model_name(model_name)
    return CURRENT_GEMINI_MODEL.set(normalized_model)


def reset_current_gemini_model(token):
    """Przywraca poprzedni model Gemini po zakończeniu żądania."""
    if token is not None:
        CURRENT_GEMINI_MODEL.reset(token)


def get_current_gemini_model():
    """Zwraca model Gemini aktywny w bieżącym kontekście żądania."""
    return CURRENT_GEMINI_MODEL.get()


def get_enabled_tag_prompt_labels(enabled_tag_types):
    """Zwraca listę opisów dozwolonych tagów do promptów Gemini."""
    return [TAG_PROMPT_LABELS[tag_type] for tag_type in enabled_tag_types]


def build_ner_tagging_rules(enabled_tag_types):
    """Buduje część promptu z zasadami tagowania dla wybranych typów encji."""
    rules = []

    if "persName" in enabled_tag_types:
        rules.extend([
            "1. Użyj znacznika <persName> dla osób. Zwróć szczególną uwagę na średniowieczne zapisy nazw gdzie czasem nie występują nazwiska, lecz zapis typu: Jan ze Żnina, Otto von Stamburg, Gijon de Stalieri. To są pełne nazwy osób, nie należy tagować osobno miejscowości, lecz całość jako osobę: <persName>Jan ze Żnina</persName>. Niekiedy będą to też same imiona osób, np. 'Bartolomeo', albo same nazwiska, np. 'Potocki'.",
        ])

    if "placeName" in enabled_tag_types:
        rules.extend([
            "2. Użyj znacznika <placeName> dla miejsc.",
            "2a. Jeśli nazwa miejscowa występuje tylko jako przymiotnik lub element tytułu/urzędu osoby, nie taguj jej osobno jako <placeName>. Przykład: 'episcopus Poznaniensis' to opis urzędu osoby, a nie samodzielne wskazanie miejsca do otagowania.",
            "2b. Jeśli nazwa miejscowa występuje w nagłówku listu lub w podpisie, np. 'Praga, 1 iulii 1393' albo 'Datum Mediolani', to również musi zostać otagowana jako <placeName>.",
        ])

    if "orgName" in enabled_tag_types:
        rules.extend([
            "3. Użyj znacznika <orgName> dla instytucji, organizacji, wspólnot i ciał kościelnych lub politycznych.",
            "3a. Frazy typu 'Romane Ecclesie', 'Ecclesia Romana', 'Sedes Apostolica', 'Curia Romana', 'Ordo Theutonicus' powinny być oznaczane jako <orgName>, a nie <placeName>.",
            "3b. Jeśli nazwa miejsca jest tylko częścią nazwy instytucji, nie taguj jej osobno jako <placeName>.",
        ])

    if "roleName" in enabled_tag_types:
        rules.extend([
            "4. Użyj znacznika <roleName> dla urzędów, funkcji, godności i określeń roli społecznej lub kościelnej.",
            "4a. Jeśli urząd lub funkcja tworzy spójną frazę, oznacz całość, np. <roleName>canonico Neapolitano</roleName> albo <roleName>cardinalis</roleName>.",
            "4b. Jeśli w obrębie <roleName> występuje przymiotnik miejscowy lub nazwa miejsca będąca częścią tytułu, nie taguj jej osobno jako <placeName>. Przykład: <roleName>episcopus Poznaniensis</roleName>, a nie osobne <placeName>Poznaniensis</placeName>.",
        ])

    if "date" in enabled_tag_types:
        rules.extend([
            "5. Użyj znacznika <date> dla dat, jeśli w tekście rzeczywiście występuje zapis daty.",
            "5a. Jeśli można bezpiecznie znormalizować datę, dodaj atrybut when w formacie ISO: pełna data <date when=\"1446-07-04\">4 iulii 1446</date>, rok i miesiąc <date when=\"1446-07\">iulii 1446</date>, sam rok <date when=\"1446\">1446</date>.",
            "5aa. Dotyczy to także łacińskich dat kalendarza rzymskiego, np. <date when=\"1393-11-18\">XIIII kalendis decemb</date>, jeśli rok wynika jasno z tego samego zapisu lub z najbliższego kontekstu.",
            "5b. Jeśli data jest zbyt niejednoznaczna, nadal możesz użyć <date>, ale bez atrybutu when.",
            "5c. Nie twórz daty z samych liczb porządkowych, numerów dokumentu lub innych liczb, które nie są rzeczywistą datą.",
        ])

    rules.extend([
        "6. NIE zmieniaj ani jednego znaku w oryginalnym tekście, zachowaj pisownię, interpunkcję i wielkość liter.",
        "7. Całość umieść wewnątrz tagu <div type=\"document\">.",
        "8. Uwzględnij podział tekstu na akapity, używając znacznika <p>.",
        "9. Otaguj wyłącznie typy tagów dozwolone w tym zadaniu. Jeśli fragment należy do wyłączonego typu, pozostaw go bez tagu.",
    ])
    return "\n".join(rules)


def build_review_prompt(raw_text, tagged_xml, enabled_tag_types):
    """Buduje prompt do drugiego passu korekcyjnego z uwzględnieniem wybranych tagów."""
    allowed_tags = "\n".join(f"- {label}" for label in get_enabled_tag_prompt_labels(enabled_tag_types))
    disabled_tag_types = [tag for tag in SUPPORTED_TEI_TAG_TYPES if tag not in enabled_tag_types]
    disabled_tags_text = ", ".join(f"<{tag}>" for tag in disabled_tag_types) or "brak"

    return f"""
Jesteś ekspertem od cyfrowej edycji tekstów historycznych, standardu TEI-XML, historykiem średniowiecza i renesansu oraz paleografem.
Otrzymujesz oryginalny tekst oraz jego wstępnie otagowaną wersję XML. Twoim zadaniem jest poprawić ten XML, nie zmieniając ani jednego znaku oryginalnego tekstu.

CELE KOREKTY:
1. Dodaj brakujące tagi tylko spośród typów dozwolonych w tym zadaniu.
2. Popraw błędne klasyfikacje tagów.
3. Zachowaj poprawne istniejące tagi i podział na akapity.

DOZWOLONE TAGI:
{allowed_tags}

TAGI WYŁĄCZONE:
{disabled_tags_text}

WAŻNE REGUŁY:
1. Jeśli typ encji jest wyłączony, pozostaw ten fragment bez tagu.
2. Instytucje nie są miejscami. Frazy typu "Romane Ecclesie", "Ecclesia Romana", "Sedes Apostolica", "Curia Romana", "Ordo Theutonicus", "Capitulum Cracoviense" powinny być <orgName>, ale tylko wtedy, gdy ten typ tagu jest dozwolony.
3. Jeśli przymiotnik miejscowy jest częścią urzędu albo instytucji, nie wydzielaj go osobno jako <placeName>.
4. Jeśli w tekście występuje rzeczywiste miejsce użyte samodzielnie, wtedy oznacz je jako <placeName>, ale tylko jeśli ten typ tagu jest dozwolony.
5. Nie zgaduj. Jeśli wyrażenie nie jest dość pewne albo należy do wyłączonego typu, zostaw je bez tagu.
6. Zwróć tylko pełny poprawiony XML wewnątrz <div type="document">.

ORYGINALNY TEKST:
---
{raw_text}
---

WSTĘPNY XML:
---
{tagged_xml}
---

Zwróć TYLKO poprawiony XML.
    """


def parse_json_object(text):
    """Wyciąga pierwszy obiekt JSON z odpowiedzi modelu."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```json|^```|```$", "", cleaned, flags=re.MULTILINE).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


def _normalize_string_list(values, *, min_length=2):
    """Usuwa duplikaty i zbyt krótkie elementy z listy napisów."""
    normalized_values = []
    seen = set()
    for value in values or []:
        value = normalize_whitespace(value)
        if len(value) < min_length:
            continue
        folded = value.lower()
        if folded in seen:
            continue
        seen.add(folded)
        normalized_values.append(value)
    return normalized_values


def get_polish_equivalent(value, tag_type):
    """Zwraca polski odpowiednik historycznej formy osoby lub miejsca."""
    normalized = normalize_for_lookup(value)
    if not normalized:
        return None
    if tag_type == "persName":
        return POLISH_PERSON_EQUIVALENTS.get(normalized)
    if tag_type == "placeName":
        return POLISH_PLACE_EQUIVALENTS.get(normalized)
    return None


def augment_with_polish_equivalents(tag_type, values):
    """Rozszerza listę wariantów o znane polskie odpowiedniki encji."""
    augmented = list(values or [])
    seen = {normalize_whitespace(value).casefold() for value in augmented if normalize_whitespace(value)}

    for value in list(augmented):
        if tag_type == "persName":
            for variant in PERSON_SEARCH_VARIANTS.get(normalize_for_lookup(value), []):
                folded = variant.casefold()
                if folded in seen:
                    continue
                seen.add(folded)
                augmented.append(variant)

        polish_equivalent = get_polish_equivalent(value, tag_type)
        if not polish_equivalent:
            continue
        folded = polish_equivalent.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        augmented.append(polish_equivalent)

    return _normalize_string_list(augmented)


def get_grounded_person_name_tokens(name, context, normalized_best):
    """Zbiera tokeny osobowe zakotwiczone w nazwie źródłowej lub w lokalnym kontekście."""
    grounded_tokens = set()
    for source_text in (name, context, normalized_best):
        grounded_tokens.update(tokenize_for_match(source_text))
        for raw_token in re.findall(r"[\w-]+", normalize_whitespace(source_text), flags=re.UNICODE):
            polish_equivalent = get_polish_equivalent(raw_token, "persName")
            if polish_equivalent:
                grounded_tokens.update(tokenize_for_match(polish_equivalent))
    return grounded_tokens


def is_grounded_person_name_variant(variant, grounded_tokens, baseline_token_count):
    """Sprawdza, czy wariant osoby nie dopowiada nowych członów nieobecnych w tekście i kontekście."""
    variant_tokens = tokenize_for_match(variant)
    if not variant_tokens:
        return False
    if len(variant_tokens) <= baseline_token_count:
        return True
    return all(token in grounded_tokens for token in variant_tokens)


def filter_groundless_person_variants(name, context, normalized_best, variants):
    """Usuwa warianty osoby, które dopowiadają nowe człony niewynikające z tekstu ani kontekstu."""
    grounded_tokens = get_grounded_person_name_tokens(name, context, normalized_best)
    baseline_token_count = max(
        len(tokenize_for_match(name)),
        len(tokenize_for_match(normalized_best)),
        1,
    )

    filtered_variants = []
    removed_variants = []
    for variant in variants or []:
        if is_grounded_person_name_variant(variant, grounded_tokens, baseline_token_count):
            filtered_variants.append(variant)
        else:
            removed_variants.append(variant)
    return filtered_variants, removed_variants


def get_polish_place_adjectival_equivalent(value):
    """Mapuje historyczną nazwę miejsca na odpowiadający jej przymiotnik."""
    normalized = normalize_for_lookup(value)
    if not normalized:
        return None
    return POLISH_PLACE_ADJECTIVAL_EQUIVALENTS.get(normalized)


def prioritize_polish_person_variants(values):
    """Ustawia polskie warianty osób przed pozostałymi formami wyszukiwania."""
    prioritized = []
    seen = set()

    for value in values or []:
        polish_equivalent = get_polish_equivalent(value, "persName")
        if polish_equivalent:
            folded = polish_equivalent.casefold()
            if folded not in seen:
                seen.add(folded)
                prioritized.append(polish_equivalent)

    for value in values or []:
        normalized_value = normalize_whitespace(value)
        if not normalized_value:
            continue
        folded = normalized_value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        prioritized.append(normalized_value)

    return prioritized


def build_plwiki_place_phrases(entity_analysis):
    """Buduje krótkie frazy miejscowe pomocne przy fallbacku w polskiej Wikipedii."""
    raw_values = list(entity_analysis.get("place_terms", [])) + list(entity_analysis.get("context_clues", []))
    phrases = []
    seen = set()

    def add_phrase(value):
        value = normalize_whitespace(value)
        if len(value) < 3:
            return
        folded = value.casefold()
        if folded in seen:
            return
        seen.add(folded)
        phrases.append(value)

    for raw_value in raw_values:
        add_phrase(get_polish_equivalent(raw_value, "placeName"))
        add_phrase(get_polish_place_adjectival_equivalent(raw_value))

        tokens = [token for token in re.split(r"[\s,():;\-]+", normalize_whitespace(raw_value)) if token]
        for token in tokens:
            add_phrase(get_polish_equivalent(token, "placeName"))
            add_phrase(get_polish_place_adjectival_equivalent(token))

    return phrases[:6]


def build_plwiki_office_phrases(entity_analysis, place_phrases):
    """Łączy urzędy i sygnały miejscowe w frazy do zapytań plwiki."""
    office_terms = list(entity_analysis.get("office_terms", []))
    phrases = []
    seen = set()

    def add_phrase(value):
        value = normalize_whitespace(value)
        if len(value) < 3:
            return
        folded = value.casefold()
        if folded in seen:
            return
        seen.add(folded)
        phrases.append(value)

    for office_term in office_terms:
        normalized_term = normalize_whitespace(office_term)
        if not normalized_term:
            continue
        lowered = normalize_for_lookup(normalized_term)
        tokens = [token for token in re.split(r"[\s,():;\-]+", lowered) if token]

        matched_roles = []
        for token in tokens:
            equivalent = PLWIKI_OFFICE_EQUIVALENTS.get(token)
            if equivalent:
                matched_roles.append(equivalent)

        for role in matched_roles:
            add_phrase(role)

        office_place_adjectives = []
        office_place_names = []
        for token in tokens:
            adjectival = get_polish_place_adjectival_equivalent(token)
            if adjectival:
                office_place_adjectives.append(adjectival)
            place_name = get_polish_equivalent(token, "placeName")
            if place_name:
                office_place_names.append(place_name)

        office_place_adjectives = _normalize_string_list(office_place_adjectives)
        office_place_names = _normalize_string_list(office_place_names)

        for role in matched_roles:
            for adjective in office_place_adjectives[:2]:
                add_phrase(f"{role} {adjective}")
            for place_name in office_place_names[:2]:
                add_phrase(f"{role} {place_name}")

    if not phrases:
        for office_term in expand_office_terms(office_terms):
            equivalent = PLWIKI_OFFICE_EQUIVALENTS.get(normalize_for_lookup(office_term))
            if equivalent:
                add_phrase(equivalent)

    if place_phrases:
        role_only_phrases = phrases[:]
        adjectival_places = [
            place_phrase for place_phrase in place_phrases
            if place_phrase == place_phrase.lower() and " " not in place_phrase
        ]
        for role_phrase in role_only_phrases:
            role_tokens = role_phrase.split()
            if len(role_tokens) != 1:
                continue
            for adjective in adjectival_places[:2]:
                add_phrase(f"{role_phrase} {adjective}")

    return phrases[:8]


def dedupe_candidates(candidates):
    """Scala kandydatów powtarzających się pod tym samym URL-em lub ID."""
    deduped = {}
    for candidate in candidates:
        key = candidate.get("url") or f"{candidate.get('source')}:{candidate.get('id')}"
        if key in deduped:
            existing = deduped[key]
            existing_queries = existing.setdefault("matched_queries", [])
            for query in candidate.get("matched_queries", []):
                if query not in existing_queries:
                    existing_queries.append(query)
            continue
        deduped[key] = candidate
    return list(deduped.values())


def get_best_lang_value(multilang_map, fallback=""):
    """Wybiera preferowaną wartość językową z listy obsługiwanych języków."""
    for lang in PREFERRED_WIKIBASE_LANGUAGES:
        value = multilang_map.get(lang)
        if value:
            return value
    return fallback


def extract_multilang_values(multilang_map):
    """Spłaszcza mapę wielojęzyczną do listy unikalnych wartości tekstowych."""
    values = []
    seen = set()
    for lang in PREFERRED_WIKIBASE_LANGUAGES:
        value = normalize_whitespace(multilang_map.get(lang, ""))
        if not value:
            continue
        folded = value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        values.append(value)
    return values


def build_multilang_map(data):
    """Wyciąga z odpowiedzi Wikibase etykiety lub opisy w obsługiwanych językach."""
    result = {}
    for lang in PREFERRED_WIKIBASE_LANGUAGES:
        value = normalize_whitespace(data.get(lang, {}).get("value", ""))
        if value:
            result[lang] = value
    return result


def build_alias_map(data):
    """Buduje znormalizowaną mapę aliasów z odpowiedzi API Wikibase."""
    result = {}
    for lang in PREFERRED_WIKIBASE_LANGUAGES:
        aliases = []
        seen = set()
        for alias in data.get(lang, []):
            value = normalize_whitespace(alias.get("value", ""))
            if not value:
                continue
            folded = value.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            aliases.append(value)
        if aliases:
            result[lang] = aliases
    return result


def extract_qid_from_title(title):
    """Wyodrębnia identyfikator Q z tytułu wyniku wyszukiwania Wikibase."""
    match = re.fullmatch(r"(?:Item:)?(Q\d+)", normalize_whitespace(title))
    if match:
        return match.group(1)
    return None


def build_fuzzy_search_query(query):
    """Zamienia zwykłe zapytanie na prostą wersję rozmytą dla Cirrusa."""
    tokens = re.findall(r"[\w-]+", normalize_whitespace(query), flags=re.UNICODE)
    if not tokens:
        return ""
    fuzzy_tokens = [f"{token}~2" for token in tokens]
    return " ".join(fuzzy_tokens)


def format_time_value(raw_value):
    """Wyciąga rok z wartości czasu zwracanej przez Wikibase."""
    raw_value = str(raw_value or "")
    match = re.search(r"([+-]?\d{3,4})", raw_value)
    if match:
        return match.group(1).lstrip("+")
    return raw_value


def extract_wikibase_time_year(raw_time):
    """Wyciąga sam rok z pola `time` zwróconego przez Wikibase."""
    if isinstance(raw_time, dict):
        raw_time = raw_time.get("time", "")
    year_text = format_time_value(raw_time)
    if not year_text or not re.fullmatch(r"-?\d{3,4}", year_text):
        return None, None
    return int(year_text), year_text


def get_wikibase_time_precision(raw_time_value):
    """Normalizuje precyzję czasu Wikibase do liczby całkowitej albo `None`."""
    if not isinstance(raw_time_value, dict):
        return None
    precision = raw_time_value.get("precision")
    try:
        return int(precision) if precision is not None else None
    except (TypeError, ValueError):
        return None


def compute_wikibase_year_bounds(year, precision):
    """Zamienia rok i precyzję Wikibase na ostrożny zakres lat."""
    if year is None:
        return None, None
    if precision is None or precision >= 9:
        return year, year
    if precision == 8:
        start_year = year if year >= 0 else year - 9
        return start_year, start_year + 9
    if precision == 7:
        if year <= 0:
            return None, None
        start_year = year - ((year - 1) % 100)
        return start_year, start_year + 99
    if precision == 6:
        if year <= 0:
            return None, None
        start_year = year - ((year - 1) % 1000)
        return start_year, start_year + 999
    return None, None


def extract_life_year_data(raw_time_value):
    """Zwraca pełniejszy opis roku życia: dokładny rok albo zakres wynikający z precyzji."""
    year, year_text = extract_wikibase_time_year(raw_time_value)
    if year is None:
        return {
            "year": None,
            "year_min": None,
            "year_max": None,
            "display": None,
            "precision": None,
        }

    precision = get_wikibase_time_precision(raw_time_value)
    year_min, year_max = compute_wikibase_year_bounds(year, precision)
    if year_min is None or year_max is None:
        return {
            "year": None,
            "year_min": None,
            "year_max": None,
            "display": None,
            "precision": precision,
        }

    display = year_text
    exact_year = year
    if year_min != year_max:
        display = f"{year_min}-{year_max}"
        exact_year = None

    return {
        "year": exact_year,
        "year_min": year_min,
        "year_max": year_max,
        "display": display,
        "precision": precision,
    }


def extract_precise_life_year(raw_time_value):
    """Zwraca dokładny rok życia tylko wtedy, gdy Wikibase podaje precyzję co najmniej do roku."""
    life_year_data = extract_life_year_data(raw_time_value)
    if life_year_data["year_min"] != life_year_data["year_max"]:
        return None
    if life_year_data["precision"] is not None and life_year_data["precision"] < 9:
        return None
    if life_year_data["year"] is None:
        return None
    return life_year_data["year"]


def extract_years_from_text(text, min_year=900, max_year=1800):
    """Zbiera unikalne lata z tekstu w zadanym przedziale historycznym."""
    years = []
    seen = set()
    for match in re.finditer(r"(?<!\d)(\d{3,4})(?!\d)", normalize_whitespace(text)):
        year = int(match.group(1))
        if year < min_year or year > max_year:
            continue
        if year in seen:
            continue
        seen.add(year)
        years.append(year)
    return years


def roman_to_int(value):
    """Konwertuje liczbę rzymską na int albo zwraca `None`, jeśli jest błędna."""
    roman = normalize_whitespace(value).upper()
    if not roman or not re.fullmatch(r"[IVXLCDM]+", roman):
        return None

    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    previous = 0
    for char in reversed(roman):
        current = values[char]
        if current < previous:
            total -= current
        else:
            total += current
            previous = current
    return total


def normalize_iso_date_value(value):
    """Normalizuje datę do ISO (`YYYY`, `YYYY-MM`, `YYYY-MM-DD`) jeśli to możliwe."""
    normalized = normalize_whitespace(value)
    if not normalized:
        return None

    normalized = normalized.replace("/", "-").replace(".", "-")

    year_only_match = re.fullmatch(r"(\d{4})", normalized)
    if year_only_match:
        return year_only_match.group(1)

    year_month_match = re.fullmatch(r"(\d{4})-(\d{1,2})", normalized)
    if year_month_match:
        year = int(year_month_match.group(1))
        month = int(year_month_match.group(2))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"
        return None

    full_date_match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", normalized)
    if full_date_match:
        year = int(full_date_match.group(1))
        month = int(full_date_match.group(2))
        day = int(full_date_match.group(3))
        try:
            datetime(year, month, day)
        except ValueError:
            return None
        return f"{year:04d}-{month:02d}-{day:02d}"

    return None


def extract_explicit_year_from_text(text):
    """Wyciąga czterocyfrowy rok bezpośrednio zapisany w tekście."""
    normalized_text = normalize_whitespace(text)
    if not normalized_text:
        return None
    year_match = re.search(r"(?<!\d)(\d{4})(?!\d)", normalized_text)
    if not year_match:
        return None
    return int(year_match.group(1))


def find_month_token_in_text(normalized_lookup_text):
    """Zwraca `(month_number, matched_token)` dla pierwszego rozpoznanego miesiąca w tekście."""
    for month_token, month_number in sorted(DATE_MONTH_TOKENS.items(), key=lambda item: (-len(item[0]), item[0])):
        pattern = rf"\b{re.escape(month_token)}\b"
        if re.search(pattern, normalized_lookup_text):
            return month_number, month_token
    return None, None


def get_roman_anchor_day(anchor_name, month):
    """Zwraca dzień miesiąca odpowiadający Kalendom, Nonom albo Idom."""
    if anchor_name == "kalends":
        return 1
    if anchor_name == "nones":
        return 7 if month in {3, 5, 7, 10} else 5
    if anchor_name == "ides":
        return 15 if month in {3, 5, 7, 10} else 13
    return None


def infer_roman_calendar_iso_date(text, fallback_year=None):
    """Próbuje zinterpretować łacińską datę w stylu rzymskim, np. `XIIII kalendis decemb`."""
    normalized_text = normalize_whitespace(text)
    if not normalized_text:
        return None

    lowered_text = normalize_for_lookup(normalized_text)
    year = extract_explicit_year_from_text(normalized_text) or fallback_year
    if year is None:
        return None

    roman_date_match = re.search(
        r"\b(?P<count>\d{1,2}|[ivxlcdm]+)\b\s+"
        r"(?P<anchor>kalend(?:is|as|ae|a)?|non(?:is|as|ae|a)?|id(?:ibus|us|as|a)?)\b",
        lowered_text,
        flags=re.IGNORECASE,
    )
    if not roman_date_match:
        return None

    month, _ = find_month_token_in_text(lowered_text)
    if month is None:
        return None

    anchor_token = roman_date_match.group("anchor")
    if anchor_token.startswith("kalend"):
        anchor_name = "kalends"
    elif anchor_token.startswith("non"):
        anchor_name = "nones"
    else:
        anchor_name = "ides"

    count_token = roman_date_match.group("count")
    count = int(count_token) if count_token.isdigit() else roman_to_int(count_token)
    if not count:
        return None

    anchor_day = get_roman_anchor_day(anchor_name, month)
    if anchor_day is None:
        return None

    anchor_date = None
    if anchor_name == "kalends":
        anchor_date = datetime(year, month, anchor_day)
    else:
        anchor_date = datetime(year, month, anchor_day)

    calculated_date = anchor_date - timedelta(days=count - 1)
    return calculated_date.strftime("%Y-%m-%d")


def infer_iso_date_value_from_text(text, fallback_year=None):
    """Próbuje odczytać z tekstu datę w postaci ISO do użycia w atrybucie `when`."""
    normalized_text = normalize_whitespace(text)
    if not normalized_text:
        return None

    year = extract_explicit_year_from_text(normalized_text) or fallback_year
    if year is None:
        return None

    lowered_text = normalize_for_lookup(normalized_text)
    roman_calendar_date = infer_roman_calendar_iso_date(normalized_text, fallback_year=year)
    if roman_calendar_date:
        return roman_calendar_date

    month, month_pattern = find_month_token_in_text(lowered_text)

    if month is None:
        return f"{year:04d}"

    day_match = re.search(
        rf"\b(?P<day>\d{{1,2}}|[ivxlcdm]+)\b\s+{re.escape(month_pattern)}\b.*?(?<!\d)(?P<year>\d{{4}})(?!\d)",
        lowered_text,
        flags=re.IGNORECASE,
    )
    if day_match:
        day_token = day_match.group("day")
        day = int(day_token) if day_token.isdigit() else roman_to_int(day_token)
        if day:
            try:
                datetime(year, month, day)
            except ValueError:
                return f"{year:04d}-{month:02d}"
            return f"{year:04d}-{month:02d}-{day:02d}"

    return f"{year:04d}-{month:02d}"


def resolve_fallback_year_for_date_tag(tag, document_years):
    """Szuka możliwie jednoznacznego roku dla daty, gdy sam tag go nie zawiera."""
    tag_text = tag.get_text(" ")
    explicit_year = extract_explicit_year_from_text(tag_text)
    if explicit_year is not None:
        return explicit_year

    parent_block = tag.find_parent(["p", "ab", "note", "head", "div"])
    if parent_block:
        context_years = extract_years_from_text(parent_block.get_text(" "))
        if len(context_years) == 1:
            return context_years[0]

    if len(document_years) == 1:
        return document_years[0]

    return None


def build_latin_day_month_year_pattern():
    """Buduje regex dla prostych łacińskich dat typu `19 decembris 1392`."""
    month_tokens = sorted(DATE_MONTH_TOKENS.keys(), key=lambda value: (-len(value), value))
    month_pattern = "|".join(re.escape(month_token) for month_token in month_tokens)
    return re.compile(
        rf"(?<![\w-])(?P<date>(?P<day>\d{{1,2}}|[ivxlcdm]{{1,8}})\s+"
        rf"(?P<month>{month_pattern})\s+"
        rf"(?P<year>[12]\d{{3}}))(?![\w-])",
        flags=re.IGNORECASE,
    )


LATIN_DAY_MONTH_YEAR_PATTERN = build_latin_day_month_year_pattern()


def text_node_can_receive_date_tag(text_node):
    """Sprawdza, czy można bezpiecznie dodać <date> w danym węźle tekstowym."""
    parent = text_node.parent
    if parent is None:
        return False
    if parent.find_parent("date") or parent.name == "date":
        return False
    if parent.name in {"key", "ref"}:
        return False
    return True


def tag_dates_in_text_node(soup, text_node):
    """Dodaje tagi <date> dla prostych dat znalezionych w pojedynczym węźle tekstowym."""
    raw_text = str(text_node)
    matches = list(LATIN_DAY_MONTH_YEAR_PATTERN.finditer(raw_text))
    if not matches:
        return 0

    inserted_count = 0
    cursor = 0
    for match in matches:
        date_text = match.group("date")
        normalized_when = infer_iso_date_value_from_text(date_text)
        if not normalized_when:
            continue

        if match.start() > cursor:
            text_node.insert_before(NavigableString(raw_text[cursor:match.start()]))

        date_tag = soup.new_tag("date")
        date_tag["when"] = normalized_when
        date_tag.string = date_text
        text_node.insert_before(date_tag)
        inserted_count += 1
        cursor = match.end()

    if inserted_count == 0:
        return 0
    if cursor < len(raw_text):
        text_node.insert_before(NavigableString(raw_text[cursor:]))
    text_node.extract()
    return inserted_count


def tag_untagged_latin_dates(tagged_xml):
    """Uzupełnia oczywiste, nieotagowane daty łacińskie pozostawione przez model."""
    soup = BeautifulSoup(tagged_xml, "xml")
    inserted_count = 0
    for text_node in list(soup.find_all(string=True)):
        if not text_node_can_receive_date_tag(text_node):
            continue
        inserted_count += tag_dates_in_text_node(soup, text_node)

    if inserted_count:
        diagnostic_log(
            f"Deterministycznie dodano {inserted_count} brakujących tagów <date> dla prostych dat łacińskich."
        )

    normalized_xml = soup.prettify(formatter="minimal")
    normalized_xml = re.sub(r"<\?xml.*?\?>", "", normalized_xml).strip()
    return normalized_xml


def normalize_tagged_dates(tagged_xml):
    """Waliduje i uzupełnia atrybuty `when` w tagach `date` zwróconych przez model."""
    soup = BeautifulSoup(tagged_xml, "xml")
    document_years = extract_years_from_text(soup.get_text(" "))
    for tag in soup.find_all("date"):
        normalized_when = normalize_iso_date_value(tag.get("when"))
        if not normalized_when:
            fallback_year = resolve_fallback_year_for_date_tag(tag, document_years)
            normalized_when = infer_iso_date_value_from_text(tag.get_text(), fallback_year=fallback_year)

        if normalized_when:
            tag["when"] = normalized_when
        elif tag.has_attr("when"):
            del tag["when"]

    normalized_xml = soup.prettify(formatter="minimal")
    normalized_xml = re.sub(r"<\?xml.*?\?>", "", normalized_xml).strip()
    return normalized_xml


def looks_like_org_name(value):
    """Ocena, czy fraza wygląda bardziej na instytucję niż na miejsce."""
    normalized = normalize_for_lookup(value)
    if not normalized:
        return False
    return any(stem in normalized for stem in ORG_NAME_SIGNAL_STEMS)


def normalize_tagged_org_names(tagged_xml):
    """Koryguje oczywiste instytucje błędnie oznaczone jako `placeName`."""
    soup = BeautifulSoup(tagged_xml, "xml")
    for tag in soup.find_all("placeName"):
        tag_text = normalize_whitespace(tag.get_text())
        if not tag_text:
            continue
        if looks_like_org_name(tag_text):
            tag.name = "orgName"

    normalized_xml = soup.prettify(formatter="minimal")
    normalized_xml = re.sub(r"<\?xml.*?\?>", "", normalized_xml).strip()
    return normalized_xml


def unwrap_disallowed_entity_tags(tagged_xml, enabled_tag_types):
    """Usuwa z XML-a tagi encji, które nie są włączone w bieżącej konfiguracji."""
    soup = BeautifulSoup(tagged_xml, "xml")
    disallowed_tag_types = [
        tag_type for tag_type in SUPPORTED_TEI_TAG_TYPES if tag_type not in enabled_tag_types
    ]
    for tag_type in disallowed_tag_types:
        for tag in list(soup.find_all(tag_type)):
            tag.unwrap()

    normalized_xml = soup.prettify(formatter="minimal")
    normalized_xml = re.sub(r"<\?xml.*?\?>", "", normalized_xml).strip()
    return normalized_xml


def cleanup_tagged_xml_output(tagged_text, enabled_tag_types=None):
    """Czyści odpowiedź modelu i doprowadza ją do spójnego XML-a roboczego."""
    enabled_tag_types = normalize_enabled_tag_types(enabled_tag_types)
    cleaned_text = str(tagged_text or "").strip()
    cleaned_text = re.sub(r"^```xml|^```|```$", "", cleaned_text, flags=re.MULTILINE).strip()
    if not cleaned_text.startswith("<div"):
        cleaned_text = f'<div type="document">{cleaned_text}</div>'
    if "orgName" in enabled_tag_types:
        cleaned_text = normalize_tagged_org_names(cleaned_text)
    if "date" in enabled_tag_types:
        cleaned_text = tag_untagged_latin_dates(cleaned_text)
        cleaned_text = normalize_tagged_dates(cleaned_text)
    return unwrap_disallowed_entity_tags(cleaned_text, enabled_tag_types)


def generate_tagged_xml_with_gemini(prompt, enabled_tag_types=None):
    """Uruchamia Gemini dla promptu tagującego i zwraca oczyszczony XML."""
    http_options = types.HttpOptions(timeout=TIMEOUT_MS)
    config = types.GenerateContentConfig(
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        http_options=http_options,
    )
    response = client.models.generate_content(
        model=get_current_gemini_model(),
        contents=prompt,
        config=config,
    )
    return cleanup_tagged_xml_output(response.text, enabled_tag_types=enabled_tag_types)


def review_tagged_xml_with_gemini(raw_text, tagged_xml, enabled_tag_types=None):
    """Drugi pass korekcyjny: uzupełnia braki i poprawia mylenie miejsc z instytucjami."""
    enabled_tag_types = normalize_enabled_tag_types(enabled_tag_types)
    prompt = build_review_prompt(raw_text, tagged_xml, enabled_tag_types)
    return generate_tagged_xml_with_gemini(prompt, enabled_tag_types=enabled_tag_types)


def normalize_form_analysis(name, tag_type, analysis, context=""):
    """Porządkuje odpowiedź Gemini do jednolitej struktury analizy encji."""
    fallback = {
        "surface": name,
        "tag_type": tag_type,
        "entity_type": "place" if tag_type == "placeName" else "person",
        "normalized_best": name,
        "confidence_form": "low",
        "lemma_candidates": [name],
        "surface_variants": [name],
        "office_terms": [],
        "place_terms": [],
        "context_clues": [],
    }
    if not isinstance(analysis, dict):
        return fallback

    normalized_best = normalize_whitespace(analysis.get("normalized_best", "") or name)
    confidence_form = normalize_whitespace(analysis.get("confidence_form", "low")).lower()
    if confidence_form not in {"high", "medium", "low"}:
        confidence_form = "low"

    lemma_candidates = _normalize_string_list(
        [normalized_best] + list(analysis.get("lemma_candidates", []) or [])
    )
    surface_variants = _normalize_string_list(
        [name, normalized_best] + list(analysis.get("surface_variants", []) or [])
    )
    lemma_candidates = augment_with_polish_equivalents(tag_type, lemma_candidates)
    surface_variants = augment_with_polish_equivalents(tag_type, surface_variants)

    if tag_type == "persName":
        lemma_candidates, removed_lemma_candidates = filter_groundless_person_variants(
            name,
            context,
            normalized_best,
            lemma_candidates,
        )
        surface_variants, removed_surface_variants = filter_groundless_person_variants(
            name,
            context,
            normalized_best,
            surface_variants,
        )
        removed_variants = _normalize_string_list(removed_lemma_candidates + removed_surface_variants, min_length=2)
        if removed_variants:
            diagnostic_log(
                f"Usunięto niezakotwiczone warianty osoby dla '{name}': {removed_variants}"
            )

    office_terms = _normalize_string_list(analysis.get("office_terms", []), min_length=3)
    place_terms = _normalize_string_list(analysis.get("place_terms", []), min_length=3)
    context_clues = _normalize_string_list(analysis.get("context_clues", []), min_length=3)

    return {
        "surface": name,
        "tag_type": tag_type,
        "entity_type": "place" if tag_type == "placeName" else "person",
        "normalized_best": normalized_best or name,
        "confidence_form": confidence_form,
        "lemma_candidates": lemma_candidates or [name],
        "surface_variants": surface_variants or [name],
        "office_terms": office_terms,
        "place_terms": place_terms,
        "context_clues": context_clues,
    }


def validate_form_analysis(name, tag_type, analysis):
    """Koryguje zbyt osobowe interpretacje przypadkowo nadane miejscom."""
    if tag_type != "placeName":
        return analysis

    normalized_best = normalize_for_lookup(analysis.get("normalized_best", name))
    clue_text = " ".join(analysis.get("context_clues", [])).casefold()
    person_markers = {
        "arcybiskup", "biskup", "bp", "cardinalis", "collector", "dux", "episcopus",
        "hetman", "kardynał", "kardynal", "król", "krol", "książę", "ksiaze",
        "nuncius", "opat", "papież", "papiez", "rex", "vir", "wojewoda"
    }
    if any(marker in normalized_best for marker in person_markers):
        diagnostic_log(
            f"Walidacja placeName '{name}': odrzucono znormalizowaną formę "
            f"'{analysis.get('normalized_best', name)}' jako zbyt osobową."
        )
        analysis["normalized_best"] = name
        analysis["confidence_form"] = "low"
    elif any(marker in clue_text for marker in person_markers):
        diagnostic_log(
            f"Walidacja placeName '{name}': zachowano ostrożnie surface form, "
            f"bo wskazówki kontekstowe są osobowe."
        )
        analysis["normalized_best"] = name
        analysis["confidence_form"] = "low"
    return analysis


# -------------------------- WIKIBASE FETCHING --------------------------------
def fetch_entities_map(source_config, entity_ids):
    """Pobiera encje (Q/P) z cache lub przez wbgetentities."""
    entity_ids = [entity_id for entity_id in dict.fromkeys(entity_ids) if entity_id]
    if not entity_ids:
        return {}

    source = source_config["source"]
    missing_ids = [entity_id for entity_id in entity_ids if (source, entity_id) not in ENTITY_CACHE]
    headers = {"User-Agent": "EdycjaCyfrowa (PHC IHPAN) - skrypt badawczy"}

    for offset in range(0, len(missing_ids), 40):
        batch = missing_ids[offset:offset + 40]
        params = {
            "action": "wbgetentities",
            "ids": "|".join(batch),
            "languages": "|".join(PREFERRED_WIKIBASE_LANGUAGES),
            "format": "json",
            "props": "labels|descriptions|aliases|claims",
        }
        response = get_json_response(source_config["api_url"], params, headers, source)
        entities = response.get("entities", {})
        for entity_id in batch:
            ENTITY_CACHE[(source, entity_id)] = entities.get(entity_id, {})

    return {
        entity_id: ENTITY_CACHE.get((source, entity_id), {})
        for entity_id in entity_ids
    }


def fetch_wikidata_sitelinks(entity_ids):
    """Pobiera i cache'uje sitelinki plwiki dla encji z Wikidaty."""
    entity_ids = [entity_id for entity_id in dict.fromkeys(entity_ids) if entity_id]
    if not entity_ids:
        return {}

    missing_ids = [entity_id for entity_id in entity_ids if entity_id not in WIKIDATA_SITELINK_CACHE]
    headers = {"User-Agent": "EdycjaCyfrowa (PHC IHPAN) - Wikipedia lead enrichment"}

    for offset in range(0, len(missing_ids), 40):
        batch = missing_ids[offset:offset + 40]
        params = {
            "action": "wbgetentities",
            "ids": "|".join(batch),
            "format": "json",
            "props": "sitelinks",
            "sitefilter": "plwiki",
        }
        response = get_json_response(WIKIBASE_SOURCES["Wikidata"]["api_url"], params, headers, "Wikidata sitelinks")
        entities = response.get("entities", {})
        for entity_id in batch:
            entity = entities.get(entity_id, {})
            sitelinks = entity.get("sitelinks", {})
            WIKIDATA_SITELINK_CACHE[entity_id] = {
                site_name: normalize_whitespace(site_data.get("title", ""))
                for site_name, site_data in sitelinks.items()
                if normalize_whitespace(site_data.get("title", ""))
            }

    return {
        entity_id: WIKIDATA_SITELINK_CACHE.get(entity_id, {})
        for entity_id in entity_ids
    }


def truncate_wikipedia_lead(text, max_chars=500):
    """Przycina lead Wikipedii, preferując pełne pierwsze zdanie."""
    text = normalize_whitespace(text)
    if len(text) <= max_chars:
        return text

    sentence_match = re.search(r"^(.{120,500}?[.!?])\s", text)
    if sentence_match:
        return sentence_match.group(1).strip()
    return text[:max_chars].rstrip(" ,;:") + "..."


def fetch_plwiki_extract(page_title):
    """Pobiera skrócony lead artykułu z polskiej Wikipedii."""
    page_title = normalize_whitespace(page_title)
    if not page_title:
        return ""
    if page_title in WIKIPEDIA_LEAD_CACHE:
        return WIKIPEDIA_LEAD_CACHE[page_title]

    headers = {"User-Agent": "EdycjaCyfrowa (PHC IHPAN) - Wikipedia lead enrichment"}
    params = {
        "action": "query",
        "prop": "extracts",
        "titles": page_title,
        "redirects": 1,
        "exintro": 1,
        "explaintext": 1,
        "format": "json",
    }
    response = get_json_response(PLWIKI_API_URL, params, headers, "plwiki extracts")
    pages = response.get("query", {}).get("pages", {})
    extract_text = ""
    for page_data in pages.values():
        extract_text = normalize_whitespace(page_data.get("extract", ""))
        if extract_text:
            break

    extract_text = truncate_wikipedia_lead(extract_text)
    WIKIPEDIA_LEAD_CACHE[page_title] = extract_text
    return extract_text


def search_plwiki_articles(query, limit=10):
    """Wyszukuje artykuły w polskiej Wikipedii dla zapytania fallbackowego."""
    query = normalize_whitespace(query)
    if not query:
        return []

    headers = {"User-Agent": "EdycjaCyfrowa (PHC IHPAN) - plwiki person fallback"}
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "format": "json",
    }
    response = get_json_response(PLWIKI_API_URL, params, headers, "plwiki search")
    return response.get("query", {}).get("search", [])


def fetch_plwiki_pages_metadata(page_ids):
    """Pobiera metadane stron plwiki wraz z QID i krótkim leadem."""
    page_ids = [str(page_id) for page_id in dict.fromkeys(page_ids) if str(page_id).isdigit()]
    if not page_ids:
        return {}

    missing_ids = [page_id for page_id in page_ids if page_id not in PLWIKI_PAGE_CACHE]
    headers = {"User-Agent": "EdycjaCyfrowa (PHC IHPAN) - plwiki person fallback"}

    for offset in range(0, len(missing_ids), 20):
        batch = missing_ids[offset:offset + 20]
        params = {
            "action": "query",
            "prop": "extracts|pageprops",
            "pageids": "|".join(batch),
            "redirects": 1,
            "exintro": 1,
            "explaintext": 1,
            "ppprop": "wikibase_item",
            "format": "json",
        }
        response = get_json_response(PLWIKI_API_URL, params, headers, "plwiki page metadata")
        pages = response.get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            PLWIKI_PAGE_CACHE[str(page_id)] = {
                "pageid": str(page_id),
                "title": normalize_whitespace(page_data.get("title", "")),
                "extract": truncate_wikipedia_lead(page_data.get("extract", "")),
                "wikibase_item": normalize_whitespace(
                    page_data.get("pageprops", {}).get("wikibase_item", "")
                ),
            }

    return {
        page_id: PLWIKI_PAGE_CACHE.get(page_id, {})
        for page_id in page_ids
    }


def extract_entity_id_values(claims, property_id):
    """Wyciąga identyfikatory encji z claimów wskazanej właściwości."""
    values = []
    for statement in claims.get(property_id, []):
        mainsnak = statement.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue")
        if not datavalue or datavalue.get("type") != "wikibase-entityid":
            continue
        value = datavalue.get("value", {})
        entity_id = value.get("id")
        if entity_id:
            values.append(entity_id)
    return list(dict.fromkeys(values))


def collect_claim_reference_ids(entity, priority_property_ids=None):
    """Zbiera ID encji referencyjnych potrzebnych do opisu claimów."""
    priority_property_ids = tuple(priority_property_ids or ())
    value_ids = []
    claims = entity.get("claims", {})

    for property_id in priority_property_ids:
        for statement in claims.get(property_id, []):
            mainsnak = statement.get("mainsnak", {})
            datavalue = mainsnak.get("datavalue")
            if not datavalue or datavalue.get("type") != "wikibase-entityid":
                continue
            entity_id = datavalue.get("value", {}).get("id")
            if entity_id:
                value_ids.append(entity_id)

    for statements in claims.values():
        for statement in statements[:2]:
            mainsnak = statement.get("mainsnak", {})
            datavalue = mainsnak.get("datavalue")
            if not datavalue or datavalue.get("type") != "wikibase-entityid":
                continue
            entity_id = datavalue.get("value", {}).get("id")
            if entity_id:
                value_ids.append(entity_id)
    return list(dict.fromkeys(value_ids))


def format_claim_value(datavalue, referenced_entities):
    """Formatuje pojedynczą wartość claimu do czytelnej postaci tekstowej."""
    if not datavalue:
        return None

    value_type = datavalue.get("type")
    value = datavalue.get("value")

    if value_type == "wikibase-entityid":
        entity_id = value.get("id")
        referenced = referenced_entities.get(entity_id, {})
        label_map = build_multilang_map(referenced.get("labels", {}))
        return get_best_lang_value(label_map, entity_id or "")

    if value_type == "string":
        return normalize_whitespace(value)

    if value_type == "monolingualtext":
        return normalize_whitespace(value.get("text", ""))

    if value_type == "time":
        return format_time_value(value.get("time", ""))

    if value_type == "quantity":
        return normalize_whitespace(value.get("amount", ""))

    return None


def build_claim_facts(entity, property_entities, referenced_entities, limit=10):
    """Tworzy krótką listę faktów tekstowych na podstawie claimów encji."""
    facts = []
    seen = set()
    for property_id, statements in entity.get("claims", {}).items():
        property_label_map = build_multilang_map(property_entities.get(property_id, {}).get("labels", {}))
        property_label = get_best_lang_value(property_label_map, property_id)
        for statement in statements[:2]:
            mainsnak = statement.get("mainsnak", {})
            value = format_claim_value(mainsnak.get("datavalue"), referenced_entities)
            if not value:
                continue
            fact = f"{property_label}: {value}"
            folded = fact.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            facts.append(fact)
            if len(facts) >= limit:
                return facts
    return facts


def build_priority_claim_facts(entity, property_entities, referenced_entities, priority_property_ids):
    """Buduje pełniejszą listę najważniejszych faktów, np. urzędów i funkcji z Wikidaty."""
    facts = []
    seen = set()
    claims = entity.get("claims", {})

    for property_id in priority_property_ids or ():
        property_label_map = build_multilang_map(property_entities.get(property_id, {}).get("labels", {}))
        property_label = get_best_lang_value(property_label_map, property_id)
        for statement in claims.get(property_id, []):
            mainsnak = statement.get("mainsnak", {})
            value = format_claim_value(mainsnak.get("datavalue"), referenced_entities)
            if not value:
                continue
            fact = f"{property_label}: {value}"
            folded = fact.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            facts.append(fact)

    return facts


def get_property_semantic_text(property_entity):
    """Łączy etykiety i opisy właściwości do prostego dopasowania semantycznego."""
    if not property_entity:
        return ""
    labels = extract_multilang_values(build_multilang_map(property_entity.get("labels", {})))
    descriptions = extract_multilang_values(build_multilang_map(property_entity.get("descriptions", {})))
    return " ".join(labels + descriptions).casefold()


def extract_person_life_years(entity, property_entities):
    """Próbuje odczytać lata urodzenia i śmierci osoby z jej claimów."""
    birth_markers = {
        "birth", "born", "date of birth", "birth date", "data urodzenia",
        "urodzenia", "natal", "geburt"
    }
    death_markers = {
        "death", "died", "date of death", "death date", "data śmierci",
        "data smierci", "śmierci", "smierci", "obit", "mort", "sterb"
    }

    birth_years = []
    death_years = []
    birth_ranges = []
    death_ranges = []

    for property_id, statements in entity.get("claims", {}).items():
        property_entity = property_entities.get(property_id, {})
        property_text = get_property_semantic_text(property_entity)
        if not property_text:
            continue

        target_list = None
        if any(marker in property_text for marker in birth_markers):
            target_list = birth_years
        elif any(marker in property_text for marker in death_markers):
            target_list = death_years

        if target_list is None:
            continue

        for statement in statements[:2]:
            mainsnak = statement.get("mainsnak", {})
            datavalue = mainsnak.get("datavalue")
            if not datavalue or datavalue.get("type") != "time":
                continue
            life_year_data = extract_life_year_data(datavalue.get("value", {}))
            if life_year_data["year_min"] is None or life_year_data["year_max"] is None:
                continue
            exact_year = life_year_data["year"]
            if exact_year is not None and exact_year not in target_list:
                target_list.append(exact_year)

            target_ranges = birth_ranges if target_list is birth_years else death_ranges
            year_range = (
                life_year_data["year_min"],
                life_year_data["year_max"],
                life_year_data["display"],
                life_year_data["precision"],
            )
            if year_range not in target_ranges:
                target_ranges.append(year_range)

    birth_year = min(birth_years) if birth_years else None
    death_year = max(death_years) if death_years else None

    birth_year_min = min((item[0] for item in birth_ranges), default=None)
    birth_year_max = max((item[1] for item in birth_ranges), default=None)
    death_year_min = min((item[0] for item in death_ranges), default=None)
    death_year_max = max((item[1] for item in death_ranges), default=None)

    birth_display = None
    death_display = None
    if birth_ranges:
        birth_display = min(
            birth_ranges,
            key=lambda item: (
                item[3] if item[3] is not None else 99,
                item[0],
            ),
        )[2]
    if death_ranges:
        death_display = max(
            death_ranges,
            key=lambda item: (
                item[3] if item[3] is not None else -1,
                item[1],
            ),
        )[2]

    return {
        "birth_year": birth_year,
        "death_year": death_year,
        "birth_year_min": birth_year_min,
        "birth_year_max": birth_year_max,
        "death_year_min": death_year_min,
        "death_year_max": death_year_max,
        "birth_display": birth_display,
        "death_display": death_display,
    }


def build_candidate_from_entity(source_config, entity_id, entity, property_entities, referenced_entities):
    """Buduje zunifikowany obiekt kandydata na podstawie encji Wikibase."""
    labels_map = build_multilang_map(entity.get("labels", {}))
    descriptions_map = build_multilang_map(entity.get("descriptions", {}))
    aliases_map = build_alias_map(entity.get("aliases", {}))

    instance_of_ids = extract_entity_id_values(entity.get("claims", {}), source_config["instance_of_property"])
    instance_of_entities = fetch_entities_map(source_config, instance_of_ids)
    instance_of_texts = []
    for class_id in instance_of_ids:
        class_entity = instance_of_entities.get(class_id, {})
        class_labels = extract_multilang_values(build_multilang_map(class_entity.get("labels", {})))
        class_descs = extract_multilang_values(build_multilang_map(class_entity.get("descriptions", {})))
        instance_of_texts.extend(class_labels)
        instance_of_texts.extend(class_descs)

    candidate = {
        "id": entity_id,
        "source": source_config["source"],
        "url": f"{source_config['entity_base_url']}/{entity_id}",
        "name": get_best_lang_value(labels_map, entity_id),
        "labels": labels_map,
        "descriptions": descriptions_map,
        "aliases": aliases_map,
        "instance_of_ids": instance_of_ids,
        "instance_of_texts": _normalize_string_list(instance_of_texts, min_length=2),
        "priority_claim_facts": build_priority_claim_facts(
            entity,
            property_entities,
            referenced_entities,
            source_config.get("priority_claim_property_ids", ()),
        ),
        "claim_facts": build_claim_facts(entity, property_entities, referenced_entities),
        "birth_year": None,
        "death_year": None,
        "birth_year_min": None,
        "birth_year_max": None,
        "death_year_min": None,
        "death_year_max": None,
        "birth_display": None,
        "death_display": None,
        "matched_queries": [],
    }
    if candidate_is_human(candidate, source_config):
        candidate.update(extract_person_life_years(entity, property_entities))
    candidate["desc"] = get_best_lang_value(descriptions_map, "Brak opisu")
    return candidate


def build_candidates_from_entity_ids(source_config, entity_ids):
    """Pobiera encje i zamienia ich identyfikatory na kandydatów do wyboru."""
    entities_map = fetch_entities_map(source_config, entity_ids)
    priority_property_ids = tuple(source_config.get("priority_claim_property_ids", ()))
    property_ids = []
    referenced_ids = []

    for entity_id in entity_ids:
        entity = entities_map.get(entity_id, {})
        property_ids.extend(priority_property_ids)
        property_ids.extend(entity.get("claims", {}).keys())
        referenced_ids.extend(collect_claim_reference_ids(entity, priority_property_ids))

    property_ids = list(dict.fromkeys(property_ids))
    referenced_ids = list(dict.fromkeys(referenced_ids))
    property_entities = fetch_entities_map(source_config, property_ids[:60])
    referenced_entities = fetch_entities_map(source_config, referenced_ids[:80])

    candidates = []
    for entity_id in entity_ids:
        entity = entities_map.get(entity_id, {})
        if not entity:
            continue
        candidates.append(
            build_candidate_from_entity(
                source_config,
                entity_id,
                entity,
                property_entities,
                referenced_entities,
            )
        )
    return candidates


def search_wikibase_special(query, source_config):
    """Wyszukuje kandydatów wyłącznie przez Special:Search/Cirrus z rozmyciem ~2."""
    fuzzy_query = build_fuzzy_search_query(query)
    if not fuzzy_query:
        return []

    headers = {"User-Agent": "EdycjaCyfrowa (PHC IHPAN) - skrypt badawczy"}
    params = {
        "action": "query",
        "list": "search",
        "srsearch": fuzzy_query,
        "srnamespace": "0|120",
        "srlimit": 8,
        "format": "json",
    }

    response = get_json_response(source_config["api_url"], params, headers, source_config["source"])
    hits = response.get("query", {}).get("search", [])
    entity_ids = []
    for hit in hits:
        entity_id = extract_qid_from_title(hit.get("title", ""))
        if entity_id:
            entity_ids.append(entity_id)

    entity_ids = list(dict.fromkeys(entity_ids))
    diagnostic_log(
        f"Special:Search '{query}' -> {source_config['source']}: {entity_ids}"
    )
    return build_candidates_from_entity_ids(source_config, entity_ids)


# ------------------------------- FILTERING -----------------------------------
def candidate_type_text(candidate):
    """Składa tekst typów encji do prostych testów klasyfikacyjnych."""
    return " ".join(candidate.get("instance_of_texts", [])).casefold()


def candidate_non_person_text(candidate):
    """Łączy pola kandydata pomocne przy wykluczaniu relacji i pojęć niebędących osobami."""
    values = [candidate.get("name", ""), candidate.get("desc", "")]
    values.extend(candidate.get("instance_of_texts", []))
    values.extend(extract_multilang_values(candidate.get("descriptions", {})))
    return " ".join(normalize_whitespace(value) for value in values if normalize_whitespace(value)).casefold()


def candidate_is_human(candidate, source_config):
    """Sprawdza, czy kandydat wygląda na osobę w danym źródle."""
    instance_of_ids = set(candidate.get("instance_of_ids", []))
    non_person_text = candidate_non_person_text(candidate)
    if any(marker in non_person_text for marker in NON_PERSON_CONCEPT_MARKERS):
        return False
    if instance_of_ids & source_config["person_type_ids"]:
        return True
    if instance_of_ids & source_config.get("place_type_ids", set()):
        return False

    type_text = candidate_type_text(candidate)
    if any(marker in type_text for marker in PLACE_TYPE_MARKERS):
        return False
    if instance_of_ids:
        return False
    return any(marker in type_text for marker in HUMAN_TYPE_MARKERS)


def candidate_is_place(candidate, source_config):
    """Sprawdza, czy kandydat wygląda na miejsce lub jednostkę terytorialną."""
    if candidate_is_human(candidate, source_config):
        return False

    instance_of_ids = set(candidate.get("instance_of_ids", []))
    if instance_of_ids & source_config["place_type_ids"]:
        return True

    type_text = candidate_type_text(candidate)
    return any(marker in type_text for marker in PLACE_TYPE_MARKERS)


def filter_candidates_by_tag(candidates, tag_type, source_config):
    """Odfiltrowuje kandydatów do typu żądanej encji TEI."""
    filtered = []
    for candidate in candidates:
        if tag_type == "persName" and candidate_is_human(candidate, source_config):
            filtered.append(candidate)
        elif tag_type == "placeName" and candidate_is_place(candidate, source_config):
            filtered.append(candidate)

    filtered_labels = [
        f"{candidate['source']}:{candidate['id']}" for candidate in filtered
    ]
    diagnostic_log(
        f"Filtrowanie {source_config['source']} dla {tag_type}: {filtered_labels}"
    )
    return filtered


def expand_office_terms(office_terms):
    """Dodaje kilka prostych wariantów urzędów pomocnych w wyszukiwaniu."""
    expansions = []
    seen = set()
    replacements = {
        "cardinalis": ["kardynał", "kardynal", "cardinal"],
        "episcopus": ["biskup", "bishop", "Bischof"],
        "archiepiscopus": ["arcybiskup", "archbishop", "Erzbischof"],
        "rex": ["król", "krol", "king"],
        "regina": ["królowa", "krolowa", "queen"],
        "dux": ["książę", "ksiaze", "duke"],
        "thesaurarius": ["skarbnik", "treasurer"],
        "thesaurarius domini pape": ["skarbnik papieski", "papal treasurer", "treasurer of the pope"],
        "pape thesaurarius": ["skarbnik papieski", "papal treasurer", "treasurer of the pope"],
    }

    for office_term in office_terms or []:
        normalized_term = normalize_whitespace(office_term)
        if len(normalized_term) < 3:
            continue
        lowered = normalized_term.casefold()
        if lowered not in seen:
            seen.add(lowered)
            expansions.append(normalized_term)
        for marker, variants in replacements.items():
            if marker in lowered:
                for variant in variants:
                    variant_lowered = variant.casefold()
                    if variant_lowered in seen:
                        continue
                    seen.add(variant_lowered)
                    expansions.append(variant)
    return expansions


def expand_place_terms(place_terms):
    """Dodaje prostsze i zmodernizowane warianty określeń miejscowych przydatne w wyszukiwaniu."""
    expansions = []
    seen = set()

    for place_term in place_terms or []:
        normalized_term = normalize_whitespace(place_term)
        if len(normalized_term) < 3:
            continue

        variants = [normalized_term]
        tokens = [token for token in re.split(r"[\s,()]+", normalized_term) if len(token) >= 3]
        if tokens:
            variants.append(tokens[-1])
            if len(tokens) >= 2:
                variants.append(" ".join(tokens[-2:]))

        expanded_variants = []
        for variant in variants:
            expanded_variants.append(variant)
            normalized_variant = normalize_for_lookup(variant)
            expanded_variants.extend(PLACE_SEARCH_VARIANTS.get(normalized_variant, []))
            polish_equivalent = get_polish_equivalent(variant, "placeName")
            if polish_equivalent:
                expanded_variants.append(polish_equivalent)
                expanded_variants.extend(
                    PLACE_SEARCH_VARIANTS.get(normalize_for_lookup(polish_equivalent), [])
                )
            adjectival_equivalent = get_polish_place_adjectival_equivalent(variant)
            if adjectival_equivalent:
                expanded_variants.append(adjectival_equivalent)

        for variant in expanded_variants:
            folded = variant.lower()
            if folded in seen:
                continue
            seen.add(folded)
            expansions.append(variant)

    return expansions


def infer_office_families(values):
    """Rozpoznaje rodziny urzędów używane w fallbacku semantycznym."""
    text = " ".join(normalize_whitespace(value) for value in values or []).casefold()
    families = []
    for family_name, family_data in SEMANTIC_FALLBACK_OFFICE_FAMILIES.items():
        if any(trigger in text for trigger in family_data["triggers"]):
            families.append(family_name)
    return families


def build_semantic_place_signals(entity_analysis):
    """Wyciąga z analizy sygnały miejscowe do wzbogacenia zapytań semantycznych."""
    signals = []
    raw_values = list(entity_analysis.get("place_terms", [])) + list(entity_analysis.get("context_clues", []))
    for value in raw_values:
        normalized_value = normalize_whitespace(value)
        if len(normalized_value) < 3:
            continue
        signals.append(normalized_value)
        for token in re.split(r"[\s,():;\-]+", normalized_value):
            token = normalize_whitespace(token)
            if len(token) < 4:
                continue
            if normalize_for_lookup(token) in GENERIC_PLACE_SIGNAL_TOKENS:
                continue
            signals.append(token)
            polish_equivalent = get_polish_equivalent(token, "placeName")
            if polish_equivalent:
                signals.append(polish_equivalent)
    return _normalize_string_list(signals, min_length=3)[:8]


def build_semantic_person_profile(entity_analysis):
    """Składa profil osoby używany przy semantycznym fallbacku Wikidaty."""
    name_variants = _normalize_string_list(
        list(entity_analysis.get("lemma_candidates", [])) +
        list(entity_analysis.get("surface_variants", [])),
        min_length=2,
    )[:4]
    office_source_values = list(entity_analysis.get("office_terms", [])) + list(entity_analysis.get("context_clues", []))
    office_families = infer_office_families(office_source_values)
    office_keywords = []
    office_entity_ids = []
    for family_name in office_families:
        office_keywords.extend(SEMANTIC_FALLBACK_OFFICE_FAMILIES[family_name]["keywords"])
        office_entity_ids.extend(SEMANTIC_FALLBACK_OFFICE_FAMILIES[family_name].get("entity_ids", []))
    office_keywords.extend(expand_office_terms(entity_analysis.get("office_terms", [])))
    office_keywords = _normalize_string_list(office_keywords, min_length=3)[:10]
    office_entity_ids = list(dict.fromkeys(office_entity_ids))
    place_signals = build_semantic_place_signals(entity_analysis)
    context_years = sorted(entity_analysis.get("context_years", []))

    return {
        "name_variants": name_variants,
        "office_families": office_families,
        "office_keywords": office_keywords,
        "office_entity_ids": office_entity_ids,
        "place_signals": place_signals,
        "year_min": min(context_years) if context_years else None,
        "year_max": max(context_years) if context_years else None,
        "context_years": context_years,
    }


def should_use_wikidata_semantic_fallback(entity_analysis, tag_type, decision, candidates):
    """Ocena, czy warto uruchomić kosztowniejszy fallback semantyczny."""
    if tag_type != "persName":
        return False, "not_persName"
    if decision.get("status") == "selected":
        return False, "already_selected"

    profile = build_semantic_person_profile(entity_analysis)
    has_office = bool(profile["office_families"] or entity_analysis.get("office_terms"))
    has_place = bool(profile["place_signals"])
    has_years = bool(profile["context_years"])
    strong_signal_count = sum([has_office, has_place, has_years])

    if strong_signal_count < 2:
        return False, "insufficient_semantic_signals"

    if not (has_office or has_place):
        return False, "missing_office_or_place_signal"

    if candidates and any(candidate.get("source") == "Wikidata" for candidate in candidates):
        return True, "standard_wikidata_failed"
    return True, "direct_semantic_retry"


def build_sparql_regex_pattern(values, *, min_length=3, limit=6):
    """Buduje fragment regexu SPARQL z listy wartości tekstowych."""
    pattern_values = []
    for value in values or []:
        normalized_value = build_casefold_lookup(value)
        if len(normalized_value) < min_length:
            continue
        escaped_value = re.escape(normalized_value)
        escaped_value = escaped_value.replace(r"\ ", " ")
        escaped_value = escape_sparql_string_literal(escaped_value)
        pattern_values.append(escaped_value)
    pattern_values = list(dict.fromkeys(pattern_values))[:limit]
    if not pattern_values:
        return ""
    return "(" + "|".join(pattern_values) + ")"


def build_sparql_contains_terms(values, *, min_length=3, limit=6):
    """Normalizuje i ogranicza listę terminów do filtrów CONTAINS w SPARQL."""
    terms = []
    for value in values or []:
        normalized_value = build_casefold_lookup(value)
        if len(normalized_value) < min_length:
            continue
        terms.append(escape_sparql_string_literal(normalized_value))
    return list(dict.fromkeys(terms))[:limit]


def build_sparql_contains_filter(variable_name, values):
    """Składa filtr SPARQL CONTAINS dla wskazanej zmiennej."""
    terms = [value for value in values or [] if value]
    if not terms:
        return ""
    conditions = [
        f'CONTAINS(LCASE(STR({variable_name})), "{term}")'
        for term in terms
    ]
    return "FILTER(" + " || ".join(conditions) + ")"


def build_wikidata_values_fragment(variable_name, entity_ids):
    """Buduje klauzulę VALUES dla listy QID-ów w zapytaniu SPARQL."""
    qids = [entity_id for entity_id in entity_ids or [] if re.fullmatch(r"Q\d+", str(entity_id))]
    if not qids:
        return ""
    values = " ".join(f"wd:{entity_id}" for entity_id in qids)
    return f"VALUES {variable_name} {{ {values} }}"


def build_wikidata_semantic_query_specs(profile):
    """Tworzy warianty zapytań SPARQL z profilu semantycznego osoby."""
    name_terms = build_sparql_contains_terms(profile.get("name_variants", []), min_length=2, limit=5)
    office_terms = build_sparql_contains_terms(profile.get("office_keywords", []), min_length=3, limit=8)
    place_terms = build_sparql_contains_terms(profile.get("place_signals", []), min_length=4, limit=6)
    office_entity_ids = list(profile.get("office_entity_ids", []))

    query_specs = []
    if name_terms and (office_entity_ids or office_terms):
        query_specs.append({
            "mode": "name_office",
            "label": "SPARQL:name+office",
            "name_terms": name_terms,
            "office_terms": office_terms,
            "office_entity_ids": office_entity_ids,
            "place_terms": [],
            "year_min": profile.get("year_min"),
            "year_max": profile.get("year_max"),
        })
    if name_terms and place_terms:
        query_specs.append({
            "mode": "name_place",
            "label": "SPARQL:name+place",
            "name_terms": name_terms,
            "office_terms": office_terms,
            "office_entity_ids": office_entity_ids,
            "place_terms": place_terms,
            "year_min": profile.get("year_min"),
            "year_max": profile.get("year_max"),
        })
    return query_specs[:2]


def build_wikidata_year_filter_fragment(year_min, year_max):
    """Buduje filtr lat życia zgodny z datami z kontekstu dokumentu."""
    if year_min is None or year_max is None:
        return ""

    lower_bound = year_min - MAX_REASONABLE_LIFESPAN_YEARS
    upper_bound = year_max + MAX_REASONABLE_LIFESPAN_YEARS
    return f"""
  OPTIONAL {{ ?person wdt:P569 ?birthDate . }}
  OPTIONAL {{ ?person wdt:P570 ?deathDate . }}
  FILTER(!BOUND(?birthDate) || YEAR(?birthDate) <= {upper_bound})
  FILTER(!BOUND(?deathDate) || YEAR(?deathDate) >= {lower_bound})
"""


def compile_wikidata_person_sparql(query_spec):
    """Składa końcowe zapytanie SPARQL dla fallbacku osoby w Wikidacie."""
    office_fragment = ""
    office_entity_ids = query_spec.get("office_entity_ids", [])
    office_terms = query_spec.get("office_terms", [])
    if office_entity_ids:
        office_values = build_wikidata_values_fragment("?officeExact", office_entity_ids)
        office_fragment = f"""
  {office_values}
  ?person (wdt:P39|wdt:P106) ?officeExact .
"""
    elif office_terms:
        office_contains_filter = build_sparql_contains_filter("?officeLabel", office_terms)
        position_contains_filter = build_sparql_contains_filter("?positionLabel", office_terms)
        office_fragment = f"""
  {{
    {{
      ?person wdt:P106 ?officeItem .
      ?officeItem rdfs:label ?officeLabel .
      FILTER(LANG(?officeLabel) IN ("pl","la"))
      {office_contains_filter}
    }}
    UNION
    {{
      ?person wdt:P39 ?positionItem .
      ?positionItem rdfs:label ?positionLabel .
      FILTER(LANG(?positionLabel) IN ("pl","la"))
      {position_contains_filter}
    }}
  }}
"""

    place_fragment = ""
    place_terms = query_spec.get("place_terms", [])
    if place_terms:
        place_position_filter = build_sparql_contains_filter("?placePositionLabel", place_terms)
        place_label_filter = build_sparql_contains_filter("?placeLabel", place_terms)
        place_fragment = f"""
  {{
    {{
      ?person wdt:P39 ?placePositionItem .
      ?placePositionItem rdfs:label ?placePositionLabel .
      FILTER(LANG(?placePositionLabel) IN ("pl","la"))
      {place_position_filter}
    }}
    UNION
    {{
      VALUES ?placeProp {{ wdt:P19 wdt:P20 wdt:P27 wdt:P551 wdt:P937 }}
      ?person ?placeProp ?placeEntity .
      ?placeEntity rdfs:label ?placeLabel .
      FILTER(LANG(?placeLabel) IN ("pl","la"))
      {place_label_filter}
    }}
  }}
"""

    year_fragment = build_wikidata_year_filter_fragment(
        query_spec.get("year_min"),
        query_spec.get("year_max"),
    )

    name_contains_filter = build_sparql_contains_filter("?personLabel", query_spec.get("name_terms", []))

    return f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX bd: <http://www.bigdata.com/rdf#>
PREFIX wikibase: <http://wikiba.se/ontology#>

SELECT DISTINCT ?person ?personLabel ?birthDate ?deathDate WHERE {{
  ?person wdt:P31 wd:Q5 .
  ?person rdfs:label ?personLabel .
  FILTER(LANG(?personLabel) IN ("pl","la"))
  {name_contains_filter}
{office_fragment}{place_fragment}{year_fragment}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "pl,la,en". }}
}}
ORDER BY ?birthDate
LIMIT 50
""".strip()


def run_wikidata_sparql_query(query):
    """Uruchamia zapytanie SPARQL na endpointcie Wikidaty."""
    headers = {
        "Accept": "application/sparql-results+json",
        "User-Agent": "EdycjaCyfrowa (PHC IHPAN) - Wikidata semantic fallback",
    }
    params = {
        "query": query,
        "format": "json",
    }
    response = get_json_response(WIKIDATA_SPARQL_URL, params, headers, "Wikidata SPARQL")
    return response.get("results", {}).get("bindings", [])


def extract_entity_ids_from_sparql_bindings(bindings):
    """Wyciąga QID-y osób z wyników zwróconych przez endpoint SPARQL."""
    entity_ids = []
    for row in bindings:
        person_value = row.get("person", {}).get("value", "")
        match = re.search(r"/(Q\d+)$", person_value)
        if match:
            entity_ids.append(match.group(1))
    return list(dict.fromkeys(entity_ids))


def build_candidate_text_corpus(candidate):
    """Buduje korpus tekstowy kandydata do prostych dopasowań słów kluczowych."""
    values = [candidate.get("name", ""), candidate.get("desc", "")]
    values.extend(extract_multilang_values(candidate.get("labels", {})))
    values.extend(extract_multilang_values(candidate.get("descriptions", {})))
    values.append(candidate.get("wikipedia_lead", ""))
    for aliases in candidate.get("aliases", {}).values():
        values.extend(aliases)
    values.extend(candidate.get("instance_of_texts", []))
    values.extend(candidate.get("priority_claim_facts", []))
    values.extend(candidate.get("claim_facts", []))
    return [normalize_for_lookup(value) for value in values if normalize_for_lookup(value)]


def candidate_matches_keywords(candidate, keywords):
    """Sprawdza, czy kandydat zawiera któreś z podanych słów kluczowych."""
    if not keywords:
        return False
    corpus = build_candidate_text_corpus(candidate)
    normalized_keywords = [normalize_for_lookup(keyword) for keyword in keywords if normalize_for_lookup(keyword)]
    for keyword in normalized_keywords:
        if any(keyword in text for text in corpus):
            return True
    return False


def candidate_office_semantic_score(candidate, profile):
    """Przyznaje punkty za zgodność kandydata z profilami urzędów."""
    score = 0
    for family_name in profile.get("office_families", []):
        family_keywords = SEMANTIC_FALLBACK_OFFICE_FAMILIES.get(family_name, {}).get("keywords", [])
        if candidate_matches_keywords(candidate, family_keywords):
            score += 6
    raw_office_keywords = profile.get("office_keywords", [])[:6]
    if candidate_matches_keywords(candidate, raw_office_keywords):
        score += 3
    return score


def candidate_place_semantic_score(candidate, profile):
    """Punktuje zgodność kandydata z sygnałami miejscowymi z kontekstu."""
    matched_signals = 0
    corpus = build_candidate_text_corpus(candidate)
    for signal in profile.get("place_signals", [])[:6]:
        normalized_signal = normalize_for_lookup(signal)
        if not normalized_signal:
            continue
        if any(normalized_signal in text for text in corpus):
            matched_signals += 1
    return min(matched_signals, 2) * 3


def candidate_specificity_penalty(candidate):
    """Kara za kandydatów zbyt ogólnych lub słabo opisanych."""
    penalty = 0
    description_text = normalize_whitespace(" ".join(extract_multilang_values(candidate.get("descriptions", {}))))
    wikipedia_lead = normalize_whitespace(candidate.get("wikipedia_lead", ""))
    aliases_count = sum(len(values) for values in candidate.get("aliases", {}).values())
    claim_facts_count = len(candidate.get("claim_facts", []))
    name_text = normalize_whitespace(candidate.get("name", ""))

    if len(description_text) < 12 and len(wikipedia_lead) < 40:
        penalty += 4
    if aliases_count == 0:
        penalty += 2
    if claim_facts_count == 0 and len(wikipedia_lead) < 40:
        penalty += 2
    if len(name_text.split()) == 1:
        penalty += 2

    return penalty


def score_semantic_fallback_candidate(candidate, profile, entity_analysis):
    """Liczy łączny wynik kandydata w fallbacku semantycznym."""
    return (
        candidate_name_quality(candidate, entity_analysis) * 5
        + candidate_temporal_rank(candidate, entity_analysis) * 5
        + candidate_office_semantic_score(candidate, profile)
        + candidate_place_semantic_score(candidate, profile)
        + len(candidate.get("matched_queries", []))
        - candidate_specificity_penalty(candidate)
    )


def filter_and_rank_semantic_fallback_candidates(candidates, profile, entity_analysis):
    """Odfiltrowuje i szereguje kandydatów zwróconych przez SPARQL fallback."""
    enriched = []
    for candidate in candidates:
        if not candidate_is_human(candidate, WIKIBASE_SOURCES["Wikidata"]):
            continue
        semantic_score = score_semantic_fallback_candidate(candidate, profile, entity_analysis)
        if semantic_score <= 0:
            continue
        candidate["semantic_fallback_score"] = semantic_score
        enriched.append(candidate)

    enriched.sort(
        key=lambda candidate: (
            -candidate.get("semantic_fallback_score", 0),
            -candidate_temporal_rank(candidate, entity_analysis),
            -candidate_name_quality(candidate, entity_analysis),
            candidate.get("name", ""),
        )
    )

    ranking_labels = [
        f"{candidate['source']}:{candidate['id']} score={candidate.get('semantic_fallback_score', 0)}"
        for candidate in enriched[:8]
    ]
    diagnostic_log(
        f"Ranking fallbacku semantycznego dla '{entity_analysis['surface']}' (persName): "
        f"{ranking_labels}"
    )
    return enriched[:8]


def collect_wikidata_semantic_fallback_candidates(entity_analysis):
    """Zbiera kandydatów z semantycznego fallbacku opartego o SPARQL."""
    profile = build_semantic_person_profile(entity_analysis)
    query_specs = build_wikidata_semantic_query_specs(profile)
    diagnostic_log(
        f"Profil fallbacku semantycznego dla '{entity_analysis['surface']}' (persName): "
        f"names={profile['name_variants']}, office_families={profile['office_families']}, "
        f"office_entity_ids={profile['office_entity_ids']}, office_keywords={profile['office_keywords'][:6]}, "
        f"place_signals={profile['place_signals'][:6]}, "
        f"context_years={profile['context_years']}"
    )

    if not query_specs:
        diagnostic_log(
            f"Fallback semantyczny dla '{entity_analysis['surface']}' (persName) pominięty: brak query specs."
        )
        return []

    collected_candidates = []
    for query_spec in query_specs:
        sparql_query = compile_wikidata_person_sparql(query_spec)
        diagnostic_log(
            f"Uruchamiam fallback semantyczny {query_spec['label']} dla '{entity_analysis['surface']}' (persName)."
        )
        diagnostic_log(
            f"Zapytanie {query_spec['label']} dla '{entity_analysis['surface']}' (persName): "
            f"{normalize_whitespace(sparql_query)}"
        )
        try:
            bindings = run_wikidata_sparql_query(sparql_query)
            entity_ids = extract_entity_ids_from_sparql_bindings(bindings)
            diagnostic_log(
                f"Wyniki {query_spec['label']} dla '{entity_analysis['surface']}' (persName): {entity_ids}"
            )
        except Exception as exc:
            diagnostic_log(
                f"Błąd fallbacku semantycznego {query_spec['label']} dla "
                f"'{entity_analysis['surface']}' (persName): {exc}"
            )
            continue

        wikidata_candidates = build_candidates_from_entity_ids(WIKIBASE_SOURCES["Wikidata"], entity_ids)
        filtered_candidates = filter_candidates_by_tag(
            wikidata_candidates,
            "persName",
            WIKIBASE_SOURCES["Wikidata"],
        )
        for candidate in filtered_candidates:
            if query_spec["label"] not in candidate["matched_queries"]:
                candidate["matched_queries"].append(query_spec["label"])
        collected_candidates.extend(filtered_candidates)

    deduped_candidates = dedupe_candidates(collected_candidates)
    diagnostic_log(
        f"Wzbogacam kandydatów fallbacku semantycznego Wikipedią dla "
        f"'{entity_analysis['surface']}' (persName) przed rankingiem."
    )
    try:
        deduped_candidates = enrich_wikidata_candidates_with_plwiki_leads(deduped_candidates, limit=12)
    except Exception as exc:
        diagnostic_log(
            f"Błąd wzbogacania fallbacku semantycznego Wikipedią dla "
            f"'{entity_analysis['surface']}' (persName): {exc}"
        )
    ranked_candidates = filter_and_rank_semantic_fallback_candidates(
        deduped_candidates,
        profile,
        entity_analysis,
    )
    diagnostic_log_temporal_candidates(entity_analysis, "persName", ranked_candidates, "wikidata_semantic_fallback")
    return ranked_candidates


def candidate_needs_wikipedia_lead(candidate):
    """Ocena, czy kandydat z Wikidaty potrzebuje wsparcia leadem z plwiki."""
    if candidate.get("source") != "Wikidata":
        return False
    description_text = " ".join(extract_multilang_values(candidate.get("descriptions", {})))
    description_text = normalize_whitespace(description_text)
    if len(description_text) >= 40:
        return False
    if len(candidate.get("claim_facts", [])) >= 4:
        return False
    return True


def should_enrich_candidates_with_wikipedia(tag_type, candidates):
    """Decyduje, czy przed wyborem warto dodać kandydatom leady z Wikipedii."""
    if tag_type != "persName":
        return False
    if len(candidates) < 2:
        return False
    return any(candidate_needs_wikipedia_lead(candidate) for candidate in candidates[:12])


def enrich_wikidata_candidates_with_plwiki_leads(candidates, limit=12):
    """Wzbogaca kandydatów z Wikidaty leadami pobranymi z polskiej Wikipedii."""
    wikidata_candidates = [
        candidate for candidate in candidates[:limit]
        if candidate_needs_wikipedia_lead(candidate)
    ]
    if not wikidata_candidates:
        return candidates

    sitelinks_map = fetch_wikidata_sitelinks([candidate["id"] for candidate in wikidata_candidates])
    for candidate in wikidata_candidates:
        plwiki_title = sitelinks_map.get(candidate["id"], {}).get("plwiki", "")
        if not plwiki_title:
            diagnostic_log(
                f"Brak plwiki sitelink dla {candidate['source']}:{candidate['id']} ({candidate.get('name', '?')})."
            )
            continue

        wikipedia_lead = fetch_plwiki_extract(plwiki_title)
        if wikipedia_lead:
            candidate["wikipedia_lead"] = wikipedia_lead
            candidate["wikipedia_source"] = "plwiki"
            diagnostic_log(
                f"Pobrano plwiki lead dla {candidate['source']}:{candidate['id']} -> '{plwiki_title}'."
            )
        else:
            diagnostic_log(
                f"Nie udało się pobrać plwiki lead dla {candidate['source']}:{candidate['id']} -> '{plwiki_title}'."
            )
    return candidates


def title_matches_person_name(title, name_variants):
    """Sprawdza, czy tytuł strony plwiki przypomina jedną z form osoby."""
    title_norm = normalize_for_lookup(title)
    if not title_norm:
        return False

    for value in name_variants or []:
        value_norm = normalize_for_lookup(value)
        if len(value_norm) < 3:
            continue
        first_token = value_norm.split()[0]
        if len(first_token) < 3:
            continue
        if first_token in title_norm:
            return True
    return False


def build_plwiki_person_fallback_queries(entity_analysis):
    """Buduje zestaw zapytań do awaryjnego wyszukiwania osób w plwiki."""
    raw_name_variants = augment_with_polish_equivalents(
        "persName",
        list(entity_analysis.get("lemma_candidates", [])) +
        list(entity_analysis.get("surface_variants", [])) +
        [entity_analysis.get("normalized_best")],
    )
    name_variants = prioritize_polish_person_variants(raw_name_variants)[:4]
    place_phrases = build_plwiki_place_phrases(entity_analysis)
    office_phrases = build_plwiki_office_phrases(entity_analysis, place_phrases)

    queries = []
    seen = set()

    def add_query(value):
        value = normalize_whitespace(value)
        if len(value) < 4:
            return
        folded = value.casefold()
        if folded in seen:
            return
        seen.add(folded)
        queries.append(value)

    strong_queries = []
    medium_queries = []
    weak_queries = []

    for name_variant in name_variants:
        for office_phrase in office_phrases[:5]:
            strong_queries.append(f"{name_variant} {office_phrase}")
        for place_phrase in place_phrases[:3]:
            medium_queries.append(f"{name_variant} {place_phrase}")
        weak_queries.append(name_variant)

    for query in strong_queries + medium_queries + weak_queries:
        add_query(query)

    return queries[:12]


def should_use_plwiki_person_fallback(entity_analysis, tag_type, decision):
    """Ocena, czy po nieudanym linkowaniu uruchamiać fallback plwiki."""
    if tag_type != "persName":
        return False, "not_persName"
    if decision.get("status") == "selected":
        return False, "already_selected"
    if not entity_analysis.get("office_terms") and not entity_analysis.get("place_terms"):
        return False, "no_office_or_place_terms"
    return True, "standard_linking_failed"


def collect_plwiki_person_fallback_candidates(entity_analysis):
    """Zbiera kandydatów osób przez polską Wikipedię i mapowanie do Wikidaty."""
    queries = build_plwiki_person_fallback_queries(entity_analysis)
    diagnostic_log(
        f"Plan fallbacku plwiki dla '{entity_analysis['surface']}' (persName): {queries}"
    )
    if not queries:
        return []

    name_variants = prioritize_polish_person_variants(
        augment_with_polish_equivalents(
            "persName",
            list(entity_analysis.get("lemma_candidates", [])) +
            list(entity_analysis.get("surface_variants", [])) +
            [entity_analysis.get("normalized_best")],
        )
    )
    page_hits = {}

    for query in queries:
        try:
            search_hits = search_plwiki_articles(query, limit=15)
        except Exception as exc:
            diagnostic_log(
                f"Błąd wyszukiwania plwiki dla '{entity_analysis['surface']}' (persName), query='{query}': {exc}"
            )
            continue

        hit_titles = [normalize_whitespace(hit.get("title", "")) for hit in search_hits]
        diagnostic_log(
            f"plwiki search '{query}' dla '{entity_analysis['surface']}' (persName): {hit_titles}"
        )

        for hit in search_hits:
            page_id = str(hit.get("pageid", "")).strip()
            title = normalize_whitespace(hit.get("title", ""))
            if not page_id or not title:
                continue
            if not title_matches_person_name(title, name_variants):
                continue

            page_entry = page_hits.setdefault(
                page_id,
                {
                    "pageid": page_id,
                    "title": title,
                    "matched_queries": [],
                },
            )
            if query not in page_entry["matched_queries"]:
                page_entry["matched_queries"].append(query)

    if not page_hits:
        diagnostic_log(
            f"Fallback plwiki nie zwrócił trafień tytułowych dla '{entity_analysis['surface']}' (persName)."
        )
        return []

    pages_metadata = fetch_plwiki_pages_metadata(page_hits.keys())
    wikidata_hits = []
    qid_to_page = {}
    for page_id, page_entry in page_hits.items():
        metadata = pages_metadata.get(page_id, {})
        wikibase_item = metadata.get("wikibase_item", "")
        if not re.fullmatch(r"Q\d+", wikibase_item):
            continue

        qid_to_page[wikibase_item] = {
            "title": metadata.get("title") or page_entry.get("title", ""),
            "extract": metadata.get("extract", ""),
            "matched_queries": page_entry.get("matched_queries", []),
        }
        wikidata_hits.append(wikibase_item)

    wikidata_hits = list(dict.fromkeys(wikidata_hits))
    diagnostic_log(
        f"Kandydaci QID z plwiki dla '{entity_analysis['surface']}' (persName): {wikidata_hits}"
    )
    if not wikidata_hits:
        return []

    candidates = build_candidates_from_entity_ids(WIKIBASE_SOURCES["Wikidata"], wikidata_hits)
    candidates = filter_candidates_by_tag(candidates, "persName", WIKIBASE_SOURCES["Wikidata"])

    for candidate in candidates:
        page_data = qid_to_page.get(candidate["id"], {})
        if page_data.get("extract"):
            candidate["wikipedia_lead"] = page_data["extract"]
            candidate["wikipedia_source"] = "plwiki_search"
        if page_data.get("title"):
            candidate["wikipedia_title"] = page_data["title"]
        for query in page_data.get("matched_queries", []):
            if query not in candidate["matched_queries"]:
                candidate["matched_queries"].append(query)

    ordered = order_candidates_for_review(dedupe_candidates(candidates), entity_analysis)[:10]
    ordered_labels = [f"{candidate['source']}:{candidate['id']}" for candidate in ordered]
    diagnostic_log(
        f"Uporządkowani kandydaci z fallbacku plwiki dla '{entity_analysis['surface']}' (persName): "
        f"{ordered_labels}"
    )
    diagnostic_log_temporal_candidates(entity_analysis, "persName", ordered, "plwiki_fallback")
    return ordered


def build_query_plan(entity_analysis):
    """Buduje plan zapytań do źródeł referencyjnych dla jednej encji."""
    queries = []
    seen = set()
    name_particle_tokens = {
        "de", "del", "della", "di", "do", "dos", "du", "van", "von", "zu", "z", "ze"
    }

    def add_query(value):
        value = normalize_whitespace(value)
        if len(value) < 2:
            return
        folded = value.lower()
        if folded in seen:
            return
        seen.add(folded)
        queries.append(value)

    def order_person_base_names(values):
        ordered = []
        ordered_seen = set()

        def add_base_name(value):
            value = normalize_whitespace(value)
            if len(value) < 2:
                return
            folded = value.casefold()
            if folded in ordered_seen:
                return
            ordered_seen.add(folded)
            ordered.append(value)

        for value in values or []:
            add_base_name(value)
            for variant in PERSON_SEARCH_VARIANTS.get(normalize_for_lookup(value), []):
                add_base_name(variant)
        return ordered

    def add_particleless_person_variants(value):
        value = normalize_whitespace(value)
        tokens = re.findall(r"[\w-]+", value, flags=re.UNICODE)
        if len(tokens) < 3:
            return

        folded_tokens = [token.casefold() for token in tokens]
        if not any(token in name_particle_tokens for token in folded_tokens[1:-1]):
            return

        particleless_tokens = [
            token for token, folded in zip(tokens, folded_tokens)
            if folded not in name_particle_tokens
        ]
        if len(particleless_tokens) < 2:
            return

        add_query(" ".join(particleless_tokens))
        first_name_equivalent = get_polish_equivalent(particleless_tokens[0], "persName")
        if first_name_equivalent:
            add_query(" ".join([first_name_equivalent] + particleless_tokens[1:]))
        add_query(particleless_tokens[-1])

    def office_query_priority(value):
        value = normalize_for_lookup(value)
        if any(marker in value for marker in ("thesaurarius", "treasurer", "skarbnik")):
            return 0
        if any(marker in value for marker in ("cardinalis", "cardinal", "kardynal", "kardynał")):
            return 1
        if any(marker in value for marker in ("episcopus", "bishop", "biskup")):
            return 2
        return 3

    add_query(entity_analysis.get("surface"))
    add_query(entity_analysis.get("normalized_best"))

    for value in entity_analysis.get("lemma_candidates", [])[:3]:
        add_query(value)

    for value in entity_analysis.get("surface_variants", [])[:2]:
        add_query(value)

    if entity_analysis.get("tag_type") == "persName":
        person_name_values = []
        for value in (
            [entity_analysis.get("surface"), entity_analysis.get("normalized_best")]
            + list(entity_analysis.get("lemma_candidates", [])[:4])
            + list(entity_analysis.get("surface_variants", [])[:3])
        ):
            normalized_value = normalize_whitespace(value)
            if normalized_value:
                person_name_values.append(normalized_value)
        for value in person_name_values:
            add_particleless_person_variants(value)

        base_names = order_person_base_names(
            entity_analysis.get("lemma_candidates", [])[:4] or [entity_analysis.get("normalized_best")]
        )[:4]
        office_terms = sorted(
            expand_office_terms(entity_analysis.get("office_terms", [])),
            key=lambda value: (office_query_priority(value), normalize_for_lookup(value)),
        )[:6]
        place_terms = expand_place_terms(entity_analysis.get("place_terms", []))[:5]
        for base_name in base_names:
            add_query(base_name)
            for office_term in office_terms[:4]:
                add_query(f"{base_name} {office_term}")
            for place_term in place_terms[:3]:
                add_query(f"{base_name} {place_term}")
            if office_terms and place_terms:
                add_query(f"{base_name} {office_terms[0]} {place_terms[0]}")
            german_office_terms = [term for term in office_terms if term[:1].isupper()]
            if german_office_terms and len(place_terms) > 1:
                for place_term in place_terms[1:3]:
                    add_query(f"{base_name} {german_office_terms[0]} {place_term}")

    if entity_analysis.get("tag_type") == "placeName":
        base_names = entity_analysis.get("lemma_candidates", [])[:2] or [entity_analysis.get("normalized_best")]
        place_terms = expand_place_terms(entity_analysis.get("place_terms", []))[:3]
        for base_name in base_names:
            for place_term in place_terms[:2]:
                add_query(f"{base_name} {place_term}")

    return queries[:32]


def candidate_name_quality(candidate, entity_analysis):
    """Punktuje zgodność nazwy kandydata z formą z tekstu i normalizacją."""
    surface = normalize_for_lookup(entity_analysis.get("surface", ""))
    normalized = normalize_for_lookup(entity_analysis.get("normalized_best", ""))
    names = [candidate.get("name", "")]
    names.extend(extract_multilang_values(candidate.get("labels", {})))
    for aliases in candidate.get("aliases", {}).values():
        names.extend(aliases)
    normalized_names = {normalize_for_lookup(name) for name in names if normalize_for_lookup(name)}

    if surface and surface in normalized_names:
        return 3
    if normalized and normalized in normalized_names:
        return 2
    if any(surface and surface in name for name in normalized_names):
        return 1
    if any(normalized and normalized in name for name in normalized_names):
        return 1
    return 0


def should_use_temporal_matching(entity_analysis):
    """Określa, czy dla danej encji chronologia powinna wpływać na ocenę kandydatów."""
    return (entity_analysis or {}).get("tag_type") == "persName"


def get_candidate_life_bounds(candidate):
    """Zwraca możliwy zakres lat urodzenia i śmierci kandydata."""
    birth_year = candidate.get("birth_year")
    death_year = candidate.get("death_year")
    return {
        "birth_min": candidate.get("birth_year_min", birth_year),
        "birth_max": candidate.get("birth_year_max", birth_year),
        "death_min": candidate.get("death_year_min", death_year),
        "death_max": candidate.get("death_year_max", death_year),
    }


def assess_candidate_temporal_fit(candidate, entity_analysis):
    """Ocena zgodności chronologicznej kandydata z latami z kontekstu."""
    if not should_use_temporal_matching(entity_analysis):
        return {"status": "not_applicable", "reason": "not_person_entity"}

    context_years = sorted(entity_analysis.get("context_years", []))
    posthumous_context = bool((entity_analysis or {}).get("posthumous_context"))
    if not context_years:
        return {"status": "unknown", "reason": "no_context_years"}

    life_bounds = get_candidate_life_bounds(candidate)
    birth_min = life_bounds["birth_min"]
    birth_max = life_bounds["birth_max"]
    death_min = life_bounds["death_min"]
    death_max = life_bounds["death_max"]

    if birth_min is None and death_max is None:
        return {"status": "unknown", "reason": "no_life_years"}

    inferred_birth_upper = None
    inferred_death_lower = None
    if birth_min is None and death_max is not None:
        inferred_birth_upper = death_max - MAX_REASONABLE_LIFESPAN_YEARS
        birth_min = inferred_birth_upper
        birth_max = inferred_birth_upper
    if death_max is None and birth_max is not None:
        inferred_death_lower = birth_max + MAX_REASONABLE_LIFESPAN_YEARS
        death_min = inferred_death_lower
        death_max = inferred_death_lower

    min_context_year = min(context_years)
    max_context_year = max(context_years)

    if death_max is not None and min_context_year > death_max:
        if posthumous_context:
            year_gap = min_context_year - death_max
            if year_gap <= POSTHUMOUS_CONTEXT_GRACE_YEARS:
                return {
                    "status": "compatible",
                    "reason": (
                        f"kontekst jest późniejszy od śmierci kandydata, ale dopuszczalny dla wzmianki pośmiertnej; "
                        f"różnica {year_gap} lat, maksymalnie {POSTHUMOUS_CONTEXT_GRACE_YEARS}"
                    ),
                }
        reason_suffix = ""
        if candidate.get("death_year_min") != candidate.get("death_year_max") and candidate.get("death_year_max") is not None:
            reason_suffix = (
                f"; zakres możliwej śmierci {candidate.get('death_year_min')}-{candidate.get('death_year_max')}"
            )
        elif inferred_death_lower is not None:
            reason_suffix = f"; przyjęto maks. długość życia {MAX_REASONABLE_LIFESPAN_YEARS} lat"
        return {
            "status": "conflict",
            "reason": f"kontekst po najpóźniejszej możliwej śmierci kandydata ({death_max}){reason_suffix}",
        }
    if birth_min is not None and max_context_year < birth_min:
        reason_suffix = ""
        if candidate.get("birth_year_min") != candidate.get("birth_year_max") and candidate.get("birth_year_min") is not None:
            reason_suffix = (
                f"; zakres możliwego urodzenia {candidate.get('birth_year_min')}-{candidate.get('birth_year_max')}"
            )
        elif inferred_birth_upper is not None:
            reason_suffix = f"; przyjęto maks. długość życia {MAX_REASONABLE_LIFESPAN_YEARS} lat"
        return {
            "status": "conflict",
            "reason": f"kontekst przed najwcześniejszym możliwym urodzeniem kandydata ({birth_min}){reason_suffix}",
        }

    if inferred_birth_upper is not None:
        return {
            "status": "compatible",
            "reason": (
                f"lata kontekstu mieszczą się w możliwym okresie życia; "
                f"oszacowano narodziny <= {birth_min} z daty śmierci i limitu {MAX_REASONABLE_LIFESPAN_YEARS} lat"
            ),
        }
    if inferred_death_lower is not None:
        return {
            "status": "compatible",
            "reason": (
                f"lata kontekstu mieszczą się w możliwym okresie życia; "
                f"oszacowano śmierć >= {death_max} z daty urodzenia i limitu {MAX_REASONABLE_LIFESPAN_YEARS} lat"
            ),
        }

    if candidate.get("birth_year_min") != candidate.get("birth_year_max") and candidate.get("birth_year_min") is not None:
        return {
            "status": "compatible",
            "reason": (
                f"lata kontekstu nie wykluczają życia kandydata; "
                f"zakres możliwego urodzenia {candidate.get('birth_year_min')}-{candidate.get('birth_year_max')}"
            ),
        }
    if candidate.get("death_year_min") != candidate.get("death_year_max") and candidate.get("death_year_min") is not None:
        return {
            "status": "compatible",
            "reason": (
                f"lata kontekstu nie wykluczają życia kandydata; "
                f"zakres możliwej śmierci {candidate.get('death_year_min')}-{candidate.get('death_year_max')}"
            ),
        }

    return {"status": "compatible", "reason": "lata kontekstu mieszczą się w możliwym okresie życia"}


def format_candidate_life_span(candidate):
    """Formatuje lata życia kandydata do zwięzłej postaci opisowej."""
    parts = []
    birth_display = candidate.get("birth_display")
    death_display = candidate.get("death_display")
    if birth_display:
        parts.append(f"ur. {birth_display}")
    elif candidate.get("birth_year") is not None:
        parts.append(f"ur. {candidate['birth_year']}")
    if death_display:
        parts.append(f"zm. {death_display}")
    elif candidate.get("death_year") is not None:
        parts.append(f"zm. {candidate['death_year']}")
    if not parts:
        return "brak danych"
    return ", ".join(parts)


def format_candidate_temporal_log_line(candidate, entity_analysis):
    """Buduje jednowierszowy wpis diagnostyczny o dopasowaniu chronologicznym."""
    temporal = assess_candidate_temporal_fit(candidate, entity_analysis)
    priority_facts = candidate.get("priority_claim_facts", [])
    priority_facts_text = f" key_facts={priority_facts}" if priority_facts else ""
    return (
        f"{candidate.get('name', '?')} "
        f"[{candidate.get('source', '?')}:{candidate.get('id', '?')}] "
        f"life={format_candidate_life_span(candidate)} "
        f"temporal={temporal['status']} "
        f"reason={temporal['reason']} "
        f"queries={candidate.get('matched_queries', [])}"
        f"{priority_facts_text}"
    )


def format_candidate_temporal_log_block(candidate, entity_analysis, position):
    """Buduje wielowierszowy, czytelniejszy opis dopasowania chronologicznego kandydata."""
    temporal = assess_candidate_temporal_fit(candidate, entity_analysis)
    lines = [
        f"{position}. {candidate.get('name', '?')} [{candidate.get('source', '?')}:{candidate.get('id', '?')}]",
        f"   life: {format_candidate_life_span(candidate)}",
        f"   temporal: {temporal['status']}",
        f"   reason: {temporal['reason']}",
        f"   queries: {candidate.get('matched_queries', [])}",
    ]
    if candidate.get("priority_claim_facts"):
        lines.append(f"   key_facts: {candidate.get('priority_claim_facts', [])}")
    return "\n".join(lines)


def diagnostic_log_temporal_candidates(entity_analysis, tag_type, candidates, scope_label):
    """Zapisuje w logu zbiorczy obraz chronologii rozważanych kandydatów."""
    if tag_type != "persName":
        return

    context_years = entity_analysis.get("context_years", [])
    if not candidates:
        diagnostic_log(
            f"Chronologia kandydatów dla '{entity_analysis['surface']}' ({tag_type}, {scope_label}): brak kandydatów"
        )
        return

    formatted_candidates = "\n".join(
        format_candidate_temporal_log_block(candidate, entity_analysis, position)
        for position, candidate in enumerate(candidates[:12], start=1)
    )
    diagnostic_log(
        f"Chronologia kandydatów dla '{entity_analysis['surface']}' ({tag_type}, {scope_label}), "
        f"context_years={context_years}:\n{formatted_candidates}"
    )


def candidate_temporal_rank(candidate, entity_analysis):
    """Zamienia ocenę chronologiczną na prosty ranking liczbowy."""
    if not should_use_temporal_matching(entity_analysis):
        return 0

    assessment = assess_candidate_temporal_fit(candidate, entity_analysis)
    if assessment["status"] == "compatible":
        return 2
    if assessment["status"] == "unknown":
        return 1
    return 0


def candidate_query_specificity(candidate, entity_analysis):
    """Punktuje kandydatów trafionych przez bardziej informacyjne zapytania."""
    score = 0
    office_terms = [normalize_for_lookup(term) for term in entity_analysis.get("office_terms", [])]
    place_terms = [
        normalize_for_lookup(term)
        for term in expand_place_terms(entity_analysis.get("place_terms", []))
    ]

    for query in candidate.get("matched_queries", []):
        query_norm = normalize_for_lookup(query)
        if not query_norm:
            continue
        token_count = len(tokenize_for_match(query))
        if token_count >= 2:
            score += 3
        if token_count >= 3:
            score += 2
        if any(term and term in query_norm for term in office_terms):
            score += 4
        if any(term and term in query_norm for term in place_terms):
            score += 2
    return score


def build_candidate_context_match_corpus(candidate):
    """Łączy pola kandydata do dopasowań sygnałów z kontekstu źródłowego."""
    values = [candidate.get("name", ""), candidate.get("desc", "")]
    values.extend(extract_multilang_values(candidate.get("labels", {})))
    values.extend(extract_multilang_values(candidate.get("descriptions", {})))
    values.append(candidate.get("wikipedia_lead", ""))
    for aliases in candidate.get("aliases", {}).values():
        values.extend(aliases)
    values.extend(candidate.get("instance_of_texts", []))
    values.extend(candidate.get("priority_claim_facts", []))
    values.extend(candidate.get("claim_facts", []))
    return " ".join(
        normalize_for_lookup(value)
        for value in values
        if normalize_for_lookup(value)
    )


def has_context_signal_match(corpus, signal):
    """Sprawdza dopasowanie sygnału lub jego informacyjnych tokenów w korpusie kandydata."""
    signal = normalize_for_lookup(signal)
    if len(signal) < 3:
        return False
    if signal in corpus:
        return True

    tokens = [
        token for token in tokenize_for_match(signal)
        if token not in GENERIC_PLACE_SIGNAL_TOKENS and token not in {"domini", "pape", "pope"}
    ]
    if not tokens:
        return False
    return any(token in corpus for token in tokens)


def candidate_context_signal_score(candidate, entity_analysis):
    """Punktuje zgodność faktów kandydata z urzędami i miejscami rozpoznanymi w kontekście."""
    corpus = build_candidate_context_match_corpus(candidate)
    if not corpus:
        return 0

    score = 0
    office_signals = _normalize_string_list(
        list(entity_analysis.get("office_terms", []))
        + expand_office_terms(entity_analysis.get("office_terms", [])),
        min_length=3,
    )
    place_signals = _normalize_string_list(
        list(entity_analysis.get("place_terms", []))
        + expand_place_terms(entity_analysis.get("place_terms", [])),
        min_length=3,
    )

    for office_signal in office_signals:
        if has_context_signal_match(corpus, office_signal):
            score += 4
            normalized_signal = normalize_for_lookup(office_signal)
            if any(marker in normalized_signal for marker in ("thesaurarius", "treasurer", "skarbnik")):
                score += 4

    for place_signal in place_signals:
        if has_context_signal_match(corpus, place_signal):
            score += 3

    return score


def has_ecclesiastical_person_context(entity_analysis):
    """Sprawdza, czy kontekst osoby wymaga profilu duchownego lub urzędnika kościelnego."""
    values = []
    values.extend(entity_analysis.get("office_terms", []))
    values.extend(entity_analysis.get("context_clues", []))
    context_text = normalize_for_lookup(" ".join(values))
    markers = {
        "abbas", "abbot", "apostolic", "apostolica", "apostolskiej", "bishop",
        "biskup", "canonicus", "canon", "cardinal", "cardinalis", "cleric",
        "duchowny", "ecclesia", "ecclesiae", "episcopus", "kardynal", "kardynał",
        "papa", "pape", "papal", "papieski", "papież", "pope", "presbyter",
        "sacerdos", "sedes apostolica", "thesaurarius", "treasurer",
    }
    return any(marker in context_text for marker in markers)


def candidate_has_ecclesiastical_profile(candidate):
    """Rozpoznaje, czy dane kandydata zawierają sygnały profilu kościelnego."""
    corpus = build_candidate_context_match_corpus(candidate)
    markers = {
        "abbas", "abbot", "apostolic", "apostolica", "bishop", "biskup",
        "canon", "canonicus", "cardinal", "cardinalis", "catholic",
        "cleric", "clergy", "duchowny", "ecclesia", "ecclesiae", "episcopus",
        "kaplan", "kapłan", "kardynal", "kardynał", "papal", "papiez",
        "papież", "pope", "presbyter", "priest", "sacerdos", "sedes apostolica",
        "thesaurarius", "treasurer",
    }
    return any(marker in corpus for marker in markers)


def candidate_has_strong_incompatible_profile(candidate):
    """Wykrywa świeckie/nowoczesne profile skrajnie niepasujące do urzędnika kościelnego."""
    corpus = build_candidate_context_match_corpus(candidate)
    incompatible_markers = {
        "actress", "aktor", "aktorka", "artist", "biochemist", "biolog",
        "businessperson", "chemist", "chemik", "chemical", "cinema",
        "diplomat", "diplomata", "filmmaker", "journalist", "model",
        "molecular", "muzyk", "physicist", "piosenkar", "politician",
        "polityk", "scientist", "sports", "sportowiec", "television",
        "united states ambassador", "zawodnik",
    }
    modern_markers = {
        "ambasador stanów zjednoczonych",
        "ambassador of the united states",
        "journalist",
        "dziennikarz",
        "united states ambassador",
    }
    modern_year_markers = {
        " ur 19", " ur 20", " ur. 19", " ur. 20", " born 19", " born 20",
        " urodzony 19", " urodzony 20", "2025",
    }
    geographic_false_friends = {
        "citizenship peru",
        "obywatelstwo peru",
        "peruvian",
        "peruwia",
        "peruwianka",
        "peruwijski",
    }
    female_markers = {
        "female", "kobieta", "płeć kobieta", "sex or gender female",
    }
    return {
        "incompatible_occupation": any(marker in corpus for marker in incompatible_markers),
        "modern_profile": (
            any(marker in corpus for marker in modern_markers)
            or any(marker in corpus for marker in modern_year_markers)
        ),
        "geographic_false_friend": any(marker in corpus for marker in geographic_false_friends),
        "female": any(marker in corpus for marker in female_markers),
    }


def candidate_manual_rejection_reason(candidate, entity_analysis):
    """Zwraca powód odrzucenia propozycji ręcznej albo `None`, jeśli jest dopuszczalna."""
    if should_use_temporal_matching(entity_analysis):
        temporal = assess_candidate_temporal_fit(candidate, entity_analysis)
        if temporal["status"] == "conflict":
            return "conflict_chronology"

    if entity_analysis.get("tag_type") != "persName":
        return None

    name_score = candidate_name_quality(candidate, entity_analysis)
    context_score = candidate_context_signal_score(candidate, entity_analysis)
    query_score = candidate_query_specificity(candidate, entity_analysis)
    ecclesiastical_context = has_ecclesiastical_person_context(entity_analysis)
    incompatible = candidate_has_strong_incompatible_profile(candidate)

    if ecclesiastical_context and not candidate_has_ecclesiastical_profile(candidate):
        if incompatible["modern_profile"]:
            return "modern_profile_for_historical_ecclesiastical_context"
        if incompatible["female"]:
            return "incompatible_gender_for_ecclesiastical_office"
        if incompatible["incompatible_occupation"]:
            return "incompatible_occupation_for_ecclesiastical_context"
        if incompatible["geographic_false_friend"] and context_score == 0:
            return "geographic_false_friend"

    if name_score == 0 and context_score == 0 and query_score <= 5:
        return "weak_textual_and_contextual_match"

    return None


def order_candidates_for_review(candidates, entity_analysis):
    """Sortuje kandydatów tak, by najlepsze opcje były na początku listy."""
    source_priority = {"WikiHum": 0, "va.wiki.kul.pl": 1, "Wikidata": 2}
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate_temporal_rank(candidate, entity_analysis),
            -candidate_context_signal_score(candidate, entity_analysis),
            -candidate_query_specificity(candidate, entity_analysis),
            -candidate_name_quality(candidate, entity_analysis),
            source_priority.get(candidate.get("source"), 9),
            -len(candidate.get("matched_queries", [])),
            candidate.get("name", ""),
        )
    )


def limit_candidates_for_review(candidates, max_count=12, min_per_source=2):
    """Ogranicza listę kandydatów, nie pozwalając jednemu źródłu całkiem wyprzeć pozostałych."""
    if len(candidates) <= max_count:
        return candidates

    selected = []
    selected_keys = set()
    sources = list(dict.fromkeys(candidate.get("source") for candidate in candidates))

    for source in sources:
        source_candidates = [
            candidate for candidate in candidates
            if candidate.get("source") == source
        ]
        for candidate in source_candidates[:min_per_source]:
            key = candidate.get("url") or f"{candidate.get('source')}:{candidate.get('id')}"
            if key in selected_keys:
                continue
            selected.append(candidate)
            selected_keys.add(key)
            if len(selected) >= max_count:
                return selected

    for candidate in candidates:
        key = candidate.get("url") or f"{candidate.get('source')}:{candidate.get('id')}"
        if key in selected_keys:
            continue
        selected.append(candidate)
        selected_keys.add(key)
        if len(selected) >= max_count:
            break

    return selected


def format_candidate_suggestion(candidate, entity_analysis):
    """Tworzy zwięzły opis kandydata do ręcznego wyboru w interfejsie."""
    temporal = assess_candidate_temporal_fit(candidate, entity_analysis)
    descriptions = extract_multilang_values(candidate.get("descriptions", {}))
    labels = extract_multilang_values(candidate.get("labels", {}))
    key_facts = candidate.get("priority_claim_facts", [])
    claim_facts = candidate.get("claim_facts", [])[:5]

    return {
        "id": candidate.get("id", ""),
        "source": candidate.get("source", ""),
        "url": candidate.get("url", ""),
        "name": candidate.get("name", ""),
        "labels": labels[:6],
        "description": descriptions[0] if descriptions else candidate.get("desc", ""),
        "life": format_candidate_life_span(candidate),
        "temporal_status": temporal["status"],
        "temporal_reason": temporal["reason"],
        "matched_queries": candidate.get("matched_queries", []),
        "key_facts": key_facts[:8],
        "claim_facts": claim_facts,
    }


def candidate_is_plausible_manual_suggestion(candidate, entity_analysis):
    """Odrzuca z listy ręcznej kandydatów wyraźnie niepasujących do kontekstu."""
    return candidate_manual_rejection_reason(candidate, entity_analysis) is None


def build_candidate_suggestions(candidates, entity_analysis, max_count=5):
    """Wybiera kandydatów, których warto pokazać historykowi do ręcznego rozstrzygnięcia."""
    ordered = order_candidates_for_review(dedupe_candidates(candidates), entity_analysis)
    plausible = []
    rejection_reasons = {}
    for candidate in ordered:
        rejection_reason = candidate_manual_rejection_reason(candidate, entity_analysis)
        if rejection_reason:
            rejection_reasons[rejection_reason] = rejection_reasons.get(rejection_reason, 0) + 1
            continue
        plausible.append(candidate)

    rejected_count = len(ordered) - len(plausible)
    if rejected_count:
        diagnostic_log(
            f"Propozycje ręczne dla '{entity_analysis.get('surface', '')}' "
            f"({entity_analysis.get('tag_type', '')}): odrzucono "
            f"{rejected_count} kandydatów po weryfikacji profilu; "
            f"powody={rejection_reasons}."
        )
    limited = limit_candidates_for_review(plausible, max_count=max_count, min_per_source=1)
    return [
        format_candidate_suggestion(candidate, entity_analysis)
        for candidate in limited
    ]


def search_source_candidates(query, tag_type, source_config):
    """Wyszukuje kandydatów w jednym źródle dla pojedynczego zapytania."""
    candidates = search_wikibase_special(query, source_config)
    filtered = filter_candidates_by_tag(candidates, tag_type, source_config)
    for candidate in filtered:
        if query not in candidate["matched_queries"]:
            candidate["matched_queries"].append(query)
    return filtered


def collect_candidates_from_sources(entity_analysis, tag_type, source_names):
    """Uruchamia plan zapytań na wielu źródłach i scala otrzymanych kandydatów."""
    queries = build_query_plan(entity_analysis)
    collected_candidates = []
    for source_name in source_names:
        source_config = WIKIBASE_SOURCES[source_name]
        for query in queries:
            try:
                collected_candidates.extend(search_source_candidates(query, tag_type, source_config))
            except Exception as exc:
                print(f"Błąd wyszukiwania {source_name} dla '{query}': {exc}")
    return order_candidates_for_review(dedupe_candidates(collected_candidates), entity_analysis)


def collect_candidates(entity_analysis, context, tag_type):
    """Zbiera kandydatów ze źródeł specjalistycznych; Wikidata jest dokładana później, jeśli to nie wystarczy."""
    del context
    queries = build_query_plan(entity_analysis)
    diagnostic_log(
        f"Plan zapytań dla '{entity_analysis['surface']}' ({tag_type}): {queries}"
    )

    local_candidates = collect_candidates_from_sources(
        entity_analysis,
        tag_type,
        ("WikiHum", "va.wiki.kul.pl"),
    )
    if local_candidates:
        local_candidate_labels = [
            f"{candidate['source']}:{candidate['id']}" for candidate in local_candidates
        ]
        diagnostic_log(
            f"Kandydaci ze źródeł specjalistycznych dla '{entity_analysis['surface']}' ({tag_type}): "
            f"{local_candidate_labels}"
        )
        diagnostic_log_temporal_candidates(entity_analysis, tag_type, local_candidates, "źródła specjalistyczne")
        return limit_candidates_for_review(local_candidates)

    diagnostic_log(
        f"Brak kandydatów ze źródeł specjalistycznych dla '{entity_analysis['surface']}' ({tag_type}); "
        f"fallback do Wikidata."
    )

    wikidata_candidates = collect_candidates_from_sources(
        entity_analysis,
        tag_type,
        ("Wikidata",),
    )
    wikidata_candidate_labels = [
        f"{candidate['source']}:{candidate['id']}" for candidate in wikidata_candidates
    ]
    diagnostic_log(
        f"Kandydaci Wikidata dla '{entity_analysis['surface']}' ({tag_type}): "
        f"{wikidata_candidate_labels}"
    )
    diagnostic_log_temporal_candidates(entity_analysis, tag_type, wikidata_candidates, "wikidata")
    return limit_candidates_for_review(wikidata_candidates)


def collect_wikidata_only_candidates(entity_analysis, tag_type):
    """Zbiera kandydatów wyłącznie z Wikidaty do drugiej, niezależnej próby identyfikacji."""
    wikidata_candidates = collect_candidates_from_sources(
        entity_analysis,
        tag_type,
        ("Wikidata",),
    )
    wikidata_retry_labels = [
        f"{candidate['source']}:{candidate['id']}" for candidate in wikidata_candidates
    ]
    diagnostic_log(
        f"Dodatkowi kandydaci Wikidata dla '{entity_analysis['surface']}' ({tag_type}): "
        f"{wikidata_retry_labels}"
    )
    diagnostic_log_temporal_candidates(entity_analysis, tag_type, wikidata_candidates, "wikidata_retry")
    return limit_candidates_for_review(wikidata_candidates)


# --------------------------- GEMINI IDENTIFICATION ----------------------------
def analyze_form_with_gemini(name, context, tag_type, document_years=None):
    """Zwraca prostą analizę formy: lemat i wskazówki z kontekstu do identyfikacji."""
    typ_encji = "postać historyczna" if tag_type == "persName" else "miejsce / kraj / region"

    prompt = f"""
Jesteś historykiem średniowiecza i renesansu oraz filologiem klasycznym. Analizujesz dokument historyczny z lat 1300-1600, zwykle po łacinie, polsku, niemiecku.
W tekście występuje encja typu {typ_encji} zapisana jako: "{name}".
Kontekst: "{context}"

Twoim zadaniem NIE jest końcowa identyfikacja encji. Masz przygotować prostą analizę do wyszukiwania w bazach referencyjnych.

ZASADY:
1. Możesz znormalizować nazwę do mianownika, ale ostrożnie.
2. Nie zgaduj pełnej tożsamości encyklopedycznej.
3. Wypisz tylko te wskazówki kontekstowe, które naprawdę wynikają z tekstu.
4. Dla osób wydziel funkcje, urzędy, relacje, daty i miejsca związane z osobą.
5. Dla miejsc wydziel relacje przestrzenne, jednostki nadrzędne, regiony, kraje itp.
6. Jeśli forma jest łacińska lub staroniemiecka i istnieje standardowy polski odpowiednik używany w historiografii, dodaj go do lemma_candidates.
7. Dotyczy to zwłaszcza często spotykanych form, np. Fridericus -> Fryderyk, Wenceslaus -> Wacław, Cracovia -> Kraków, Prussia -> Prusy, Polonia -> Polska.
8. Zwróć wyłącznie JSON.

Zwróć dokładnie pola:
- "normalized_best": ostrożna forma podstawowa do wyszukiwania
- "confidence_form": "high", "medium" lub "low"
- "lemma_candidates": 1-3 ostrożne warianty bazowe
- "surface_variants": 1-4 warianty pisowni
- "office_terms": lista funkcji lub urzędów, jeśli występują
- "place_terms": lista określeń miejscowych lub relacyjnych, jeśli występują
- "context_clues": krótka lista faktów z kontekstu użytecznych przy identyfikacji

Przykład dla osoby:
{{
  "normalized_best": "Fridericus",
  "confidence_form": "medium",
  "lemma_candidates": ["Fridericus", "Fryderyk"],
  "surface_variants": ["Fridericus", "Fryderyk"],
  "office_terms": ["cardinalis"],
  "place_terms": [],
  "context_clues": ["posiada godność kardynała"]
}}

Przykład dla miejsca:
{{
  "normalized_best": "Cracovia",
  "confidence_form": "medium",
  "lemma_candidates": ["Cracovia", "Kraków"],
  "surface_variants": ["Cracoviam", "Cracovia", "Kraków"],
  "office_terms": [],
  "place_terms": [],
  "context_clues": ["miasto stołeczne Królestwa Polskiego"]
}}
    """

    try:
        http_options = types.HttpOptions(timeout=TIMEOUT_MS)
        config = types.GenerateContentConfig(
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=http_options,
        )
        response = client.models.generate_content(
            model=get_current_gemini_model(),
            contents=prompt,
            config=config,
        )

        analysis = parse_json_object(response.text)
        normalized = normalize_form_analysis(name, tag_type, analysis, context=context)
        normalized = validate_form_analysis(name, tag_type, normalized)
        local_context_years = extract_years_from_text(context)
        merged_context_years = sorted(set(local_context_years + list(document_years or [])))
        normalized["context_years"] = merged_context_years
        normalized["posthumous_context"] = has_posthumous_context(context, normalized.get("context_clues", []))
        diagnostic_log(
            f"Analiza encji '{name}' ({tag_type}): normalized_best='{normalized['normalized_best']}', "
            f"confidence={normalized['confidence_form']}, lemma_candidates={normalized['lemma_candidates']}, "
            f"surface_variants={normalized['surface_variants']}, office_terms={normalized['office_terms']}, "
            f"place_terms={normalized['place_terms']}, context_clues={normalized['context_clues']}, "
            f"context_years={normalized['context_years']}, posthumous_context={normalized['posthumous_context']}"
        )
        return normalized
    except Exception as exc:
        print(f"Błąd analizy Gemini dla {name}: {exc}")
        fallback = normalize_form_analysis(name, tag_type, None, context=context)
        local_context_years = extract_years_from_text(context)
        merged_context_years = sorted(set(local_context_years + list(document_years or [])))
        fallback["context_years"] = merged_context_years
        fallback["posthumous_context"] = has_posthumous_context(context, fallback.get("context_clues", []))
        diagnostic_log(
            f"Fallback analizy encji '{name}' ({tag_type}): "
            f"normalized_best='{fallback['normalized_best']}', confidence={fallback['confidence_form']}, "
            f"context_years={fallback['context_years']}, posthumous_context={fallback['posthumous_context']}"
        )
        return fallback


def analyze_name_with_gemini(name, context, tag_type, document_years=None):
    """Zachowuje starszy interfejs, delegując do analizy formy encji."""
    return analyze_form_with_gemini(name, context, tag_type, document_years=document_years)


def normalize_name_with_gemini(name, context, tag_type, document_years=None):
    """Zwraca tylko najlepszą znormalizowaną formę nazwy encji."""
    return analyze_name_with_gemini(name, context, tag_type, document_years=document_years)["normalized_best"]


def format_candidate_for_prompt(candidate, entity_analysis=None):
    """Formatuje kandydata do bogatego opisu przekazywanego modelowi Gemini."""
    entity_analysis = entity_analysis or {}
    labels = extract_multilang_values(candidate.get("labels", {}))
    aliases = []
    for lang in PREFERRED_WIKIBASE_LANGUAGES:
        aliases.extend(candidate.get("aliases", {}).get(lang, []))
    aliases = _normalize_string_list(aliases, min_length=2)[:12]
    instance_of_texts = candidate.get("instance_of_texts", [])[:8]
    priority_claim_facts = candidate.get("priority_claim_facts", [])
    claim_facts = candidate.get("claim_facts", [])[:10]
    descriptions = extract_multilang_values(candidate.get("descriptions", {}))
    wikipedia_lead = normalize_whitespace(candidate.get("wikipedia_lead", ""))
    wikipedia_title = normalize_whitespace(candidate.get("wikipedia_title", ""))
    matched_queries = candidate.get("matched_queries", [])
    temporal_assessment = assess_candidate_temporal_fit(candidate, entity_analysis)
    use_temporal_matching = should_use_temporal_matching(entity_analysis)
    life_span_text = format_candidate_life_span(candidate)

    lines = [
        f"Źródło: {candidate['source']}",
        f"ID: {candidate['id']}",
        f"URL: {candidate['url']}",
        f"Nazwa główna: {candidate['name']}",
        f"Etykiety: {labels or ['brak']}",
        f"Opisy: {descriptions or ['brak']}",
        f"Aliasy: {aliases or ['brak']}",
        f"Instance of: {instance_of_texts or ['brak']}",
        f"Najważniejsze urzędy/funkcje z właściwości: {priority_claim_facts or ['brak']}",
    ]
    if use_temporal_matching:
        lines.append(f"Lata życia: {life_span_text}")
        lines.append(
            f"Ocena chronologiczna: {temporal_assessment['status']} ({temporal_assessment['reason']})"
        )
    else:
        lines.append("Chronologia: nie dotyczy (encja nie jest osobą)")

    lines.extend(
        [
            f"Fakty z właściwości: {claim_facts or ['brak']}",
            f"Tytuł Wikipedia: {wikipedia_title or 'brak'}",
            f"Wikipedia lead ({candidate.get('wikipedia_source', 'brak')}): {wikipedia_lead or 'brak'}",
            f"Zapytania, które dały wynik: {matched_queries or ['brak']}",
        ]
    )
    return "\n".join(lines) + "\n"


def normalize_gemini_candidate_selection(text):
    """Porządkuje odpowiedź Gemini o wyborze kandydata do postaci ułatwiającej logowanie."""
    raw_text = text or ""
    cleaned = normalize_whitespace(raw_text)
    fallback = {
        "status": "unknown",
        "selected_url": None,
        "reason": "",
        "matched_signals": [],
        "raw_response": cleaned,
    }
    if not cleaned:
        return fallback

    if cleaned.upper() == "NONE":
        fallback["status"] = "none"
        fallback["reason"] = "Gemini nie znalazło kandydata pasującego wystarczająco dobrze."
        return fallback

    try:
        parsed = parse_json_object(raw_text)
    except Exception:
        parsed = None

    if isinstance(parsed, dict):
        selected_url = normalize_whitespace(str(parsed.get("selected_url", "") or ""))
        if selected_url.upper() == "NONE":
            selected_url = ""
        if selected_url and not re.match(r"^https?://", selected_url):
            url_match = re.search(r"https?://\S+", selected_url)
            selected_url = url_match.group(0).rstrip(".,);") if url_match else ""

        matched_signals = _normalize_string_list(parsed.get("matched_signals", []), min_length=2)[:4]
        reason = normalize_whitespace(parsed.get("reason", ""))
        selection_status = "selected" if selected_url else "none"
        return {
            "status": selection_status,
            "selected_url": selected_url or None,
            "reason": reason,
            "matched_signals": matched_signals,
            "raw_response": cleaned,
        }

    url_match = re.search(r"https?://\S+", cleaned)
    if url_match:
        fallback["status"] = "selected"
        fallback["selected_url"] = url_match.group(0).rstrip(".,);")
        return fallback
    return fallback


def choose_candidate_with_gemini(name, context, tag_type, entity_analysis, candidates):
    """Prosi Gemini o wybór najlepszego kandydata i zapisuje zwięzłe uzasadnienie wyboru."""
    if not candidates:
        return {
            "selected_url": None,
            "reason": "",
            "matched_signals": [],
            "raw_response": "",
        }

    candidate_blocks = []
    for idx, candidate in enumerate(candidates[:12], start=1):
        candidate_blocks.append(f"OPCJA {idx}\n{format_candidate_for_prompt(candidate, entity_analysis)}")
    candidates_text = "\n\n".join(candidate_blocks)

    if tag_type == "persName":
        extra_clues_block = (
            f"Funkcje/urzędy: {entity_analysis.get('office_terms', [])}\n"
            f"Wskazówki miejscowe: {entity_analysis.get('place_terms', [])}\n"
            f"Lata wykryte w kontekście: {entity_analysis.get('context_years', [])}\n"
        )
        type_specific_rules = (
            "2. Dla persName wybieraj wyłącznie człowieka, zgodnego z kontekstem.\n"
            "3. Dla persName uwzględnij zgodność chronologiczną: kandydat żyjący wyraźnie przed lub po latach z kontekstu powinien być odrzucany.\n"
            "3a. Jeśli kontekst wyraźnie wskazuje wzmiankę pośmiertną (np. bone memorie, felicis recordacionis, sprawa majątku po zmarłym), data dokumentu może być nieco późniejsza od daty śmierci kandydata.\n"
            "3b. Osoby duchowne mogły kolejno pełnić różne biskupstwa i urzędy kurialne. Nie odrzucaj kandydata wyłącznie dlatego, że w danych ma inną diecezję niż w kontekście, jeśli zgadzają się imię/nazwa, rzadki urząd kurialny, chronologia i ogólny profil osoby.\n"
        )
    else:
        extra_clues_block = (
            f"Wskazówki miejscowe: {entity_analysis.get('place_terms', [])}\n"
            f"Relacje i fakty z kontekstu: {entity_analysis.get('context_clues', [])}\n"
        )
        type_specific_rules = (
            "2. Dla placeName wybieraj wyłącznie miejsce, kraj, region lub jednostkę administracyjną zgodną z kontekstem.\n"
            "3. Nie używaj kryteriów biograficznych ani chronologii osoby; liczą się przede wszystkim relacje przestrzenne, jednostki nadrzędne, aliasy i opisy.\n"
        )

    prompt = f"""
Zadanie: wybierz najlepszą encję referencyjną dla encji historycznej z tekstu albo wskaż brak rozstrzygnięcia.

Typ encji: {tag_type}
Forma z tekstu: "{name}"
Forma znormalizowana do wyszukiwania: "{entity_analysis.get('normalized_best', name)}"
Kontekst zdania/akapitu: "{context}"
Wskazówki z kontekstu: {entity_analysis.get('context_clues', [])}
{extra_clues_block}

ZASADY:
1. Nie zgaduj na siłę.
{type_specific_rules}4. Uwzględnij formę łacińską, mianownik, opisy, etykiety, aliasy, instance of i fakty z właściwości.
5. Jeśli żaden kandydat nie pasuje wystarczająco dobrze, ustaw "selected_url" na "NONE".
6. Jeśli jedna opcja pasuje wyraźnie najlepiej, wskaż jej pełny URL.
7. Uzasadnienie ma być krótkie i oparte wyłącznie na danych z kontekstu i z opisów kandydatów.

Kandydaci:
{candidates_text}

Zwróć wyłącznie JSON w postaci:
{{
  "selected_url": "pełny URL wybranego kandydata" lub "NONE",
  "reason": "krótkie uzasadnienie wyboru albo odrzucenia wszystkich kandydatów",
  "matched_signals": ["maksymalnie 3 krótkie sygnały, które zadecydowały o wyborze"]
}}
    """

    try:
        http_options = types.HttpOptions(timeout=TIMEOUT_MS)
        config = types.GenerateContentConfig(
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=http_options,
        )
        response = client.models.generate_content(
            model=get_current_gemini_model(),
            contents=prompt,
            config=config,
        )
        selection = normalize_gemini_candidate_selection(response.text)
        selection_status = selection.get("status", "unknown")
        selected_url = selection.get("selected_url")
        reason = selection.get("reason")
        matched_signals = selection.get("matched_signals", [])
        if selection_status == "selected" and selected_url:
            return selection
        if selection_status == "none":
            diagnostic_log(
                f"Gemini nie wybrało kandydata dla '{name}' ({tag_type}); "
                f"reason={reason or 'brak'}; matched_signals={matched_signals or ['brak']}"
            )
            return selection
        diagnostic_log(
            f"Gemini zwróciło nieoczekiwany wynik dla '{name}' ({tag_type}): {selection.get('raw_response', '')}"
        )
        return selection
    except Exception as exc:
        print(f"Błąd Gemini przy identyfikacji {name}: {exc}")
        return {
            "selected_url": None,
            "reason": "",
            "matched_signals": [],
            "raw_response": "",
        }


def ask_gemini_to_disambiguate(name, name_n, context, candidates, entity_analysis=None):
    """Utrzymuje zgodność ze starszym API rozstrzygania wieloznaczności."""
    del name_n
    tag_type = "persName"
    if entity_analysis and entity_analysis.get("entity_type") == "place":
        tag_type = "placeName"
    selection = choose_candidate_with_gemini(name, context, tag_type, entity_analysis or {}, candidates)
    return selection.get("selected_url")


def build_link_decision(name, context, tag_type, entity_analysis, candidates):
    """Buduje końcową decyzję linkującą na podstawie kandydatów i Gemini."""
    if not candidates:
        diagnostic_log(
            f"Brak kandydatów do identyfikacji dla '{name}' ({tag_type})."
        )
        return {
            "status": "none",
            "selected_url": None,
            "reason": "no_candidates",
        }

    if should_enrich_candidates_with_wikipedia(tag_type, candidates):
        diagnostic_log(
            f"Wzbogacam kandydatów Wikipedią dla '{name}' ({tag_type}) przed decyzją Gemini."
        )
        try:
            candidates = enrich_wikidata_candidates_with_plwiki_leads(candidates)
        except Exception as exc:
            diagnostic_log(
                f"Błąd wzbogacania kandydatów Wikipedią dla '{name}' ({tag_type}): {exc}"
            )

    selection = choose_candidate_with_gemini(
        name,
        context,
        tag_type,
        entity_analysis,
        candidates,
    )
    selected_url = selection.get("selected_url")
    if selected_url:
        selected_candidate = next(
            (candidate for candidate in candidates if candidate.get("url") == selected_url),
            None
        )
        if selected_candidate is not None:
            selection_reason = normalize_whitespace(selection.get("reason", ""))
            matched_signals = selection.get("matched_signals", [])
            diagnostic_log(
                f"Wybrany kandydat dla '{name}' ({tag_type}): "
                f"{format_candidate_temporal_log_line(selected_candidate, entity_analysis)}"
            )
            if selection_reason or matched_signals:
                diagnostic_log(
                    f"Uzasadnienie Gemini dla '{name}' ({tag_type}): "
                    f"reason={selection_reason or 'brak'}; matched_signals={matched_signals or ['brak']}"
                )
        return {
            "status": "selected",
            "selected_url": selected_url,
            "reason": "gemini_selected",
            "selection_reason": selection.get("reason", ""),
            "matched_signals": selection.get("matched_signals", []),
            "selected_candidate": (
                format_candidate_suggestion(selected_candidate, entity_analysis)
                if selected_candidate is not None else None
            ),
        }
    return {
        "status": "none",
        "selected_url": None,
        "reason": "gemini_none",
        "selection_reason": selection.get("reason", ""),
        "matched_signals": selection.get("matched_signals", []),
    }


def link_entity(name, context, tag_type, document_years=None):
    """Centralny, prosty pipeline linkowania jednej encji dla aplikacji webowej."""
    entity_analysis = analyze_name_with_gemini(name, context, tag_type, document_years=document_years)
    normalized_name = entity_analysis["normalized_best"]
    queries = build_query_plan(entity_analysis)
    candidates = collect_candidates(entity_analysis, context, tag_type)
    suggestion_candidates = list(candidates)
    decision = build_link_decision(name, context, tag_type, entity_analysis, candidates)

    used_wikidata = any(candidate.get("source") == "Wikidata" for candidate in candidates)
    has_local_candidates = any(candidate.get("source") in {"WikiHum", "va.wiki.kul.pl"} for candidate in candidates)
    should_retry_with_wikidata = (
        decision.get("status") != "selected"
        and has_local_candidates
        and not used_wikidata
    )

    if should_retry_with_wikidata:
        diagnostic_log(
            f"Kandydaci ze źródeł specjalistycznych dla '{name}' ({tag_type}) nie zostali wybrani; "
            f"ponawiam identyfikację tylko na kandydatach z Wikidaty."
        )
        candidates = collect_wikidata_only_candidates(entity_analysis, tag_type)
        suggestion_candidates.extend(candidates)
        decision = build_link_decision(name, context, tag_type, entity_analysis, candidates)

    if ENABLE_WIKIDATA_SEMANTIC_FALLBACK:
        should_use_semantic_fallback, semantic_fallback_reason = should_use_wikidata_semantic_fallback(
            entity_analysis,
            tag_type,
            decision,
            candidates,
        )
        if should_use_semantic_fallback:
            diagnostic_log(
                f"Uruchamiam fallback semantyczny Wikidata dla '{name}' ({tag_type}); "
                f"powód={semantic_fallback_reason}."
            )
            semantic_candidates = collect_wikidata_semantic_fallback_candidates(entity_analysis)
            if semantic_candidates:
                candidates = semantic_candidates
                suggestion_candidates.extend(candidates)
                decision = build_link_decision(name, context, tag_type, entity_analysis, candidates)
            else:
                diagnostic_log(
                    f"Fallback semantyczny Wikidata nie zwrócił kandydatów dla '{name}' ({tag_type})."
                )
        elif decision.get("status") != "selected":
            diagnostic_log(
                f"Fallback semantyczny Wikidata pominięty dla '{name}' ({tag_type}); "
                f"powód={semantic_fallback_reason}."
            )
    elif decision.get("status") != "selected":
        diagnostic_log(
            f"Fallback semantyczny Wikidata jest wyłączony dla '{name}' ({tag_type})."
        )

    should_use_plwiki_fallback, plwiki_fallback_reason = should_use_plwiki_person_fallback(
        entity_analysis,
        tag_type,
        decision,
    )
    if should_use_plwiki_fallback:
        diagnostic_log(
            f"Uruchamiam fallback plwiki dla '{name}' ({tag_type}); "
            f"powód={plwiki_fallback_reason}."
        )
        plwiki_candidates = collect_plwiki_person_fallback_candidates(entity_analysis)
        if plwiki_candidates:
            candidates = plwiki_candidates
            suggestion_candidates.extend(candidates)
            decision = build_link_decision(name, context, tag_type, entity_analysis, candidates)
        else:
            diagnostic_log(
                f"Fallback plwiki nie zwrócił kandydatów dla '{name}' ({tag_type})."
            )
    elif decision.get("status") != "selected":
        diagnostic_log(
            f"Fallback plwiki pominięty dla '{name}' ({tag_type}); "
            f"powód={plwiki_fallback_reason}."
        )

    return {
        "entity_analysis": entity_analysis,
        "normalized_name": normalized_name,
        "query_plan": queries,
        "candidates": candidates,
        "candidate_suggestions": build_candidate_suggestions(suggestion_candidates, entity_analysis),
        "decision": decision,
    }


# ---------------------------------- NER --------------------------------------
def tag_entities_with_gemini(raw_text, enabled_tag_types=None):
    """
    Wykorzystuje Gemini do rozpoznania osób, miejsc, dat, funkcji i instytucji w tekście
    i otagowania ich zgodnie ze standardem TEI.
    """
    enabled_tag_types = normalize_enabled_tag_types(enabled_tag_types)
    if not enabled_tag_types:
        raise ValueError("Wybierz co najmniej jeden typ tagu do rozpoznawania encji.")

    enabled_tag_labels = "\n".join(
        f"- {label}" for label in get_enabled_tag_prompt_labels(enabled_tag_types)
    )
    disabled_tag_types = [tag_type for tag_type in SUPPORTED_TEI_TAG_TYPES if tag_type not in enabled_tag_types]
    disabled_tag_labels = ", ".join(f"<{tag_type}>" for tag_type in disabled_tag_types) or "brak"
    tagging_rules = build_ner_tagging_rules(enabled_tag_types)

    prompt = f"""
Jesteś ekspertem od cyfrowej edycji tekstów historycznych, standardu TEI-XML, historykiem średniowiecza i renesansu oraz paleografem.
Twoim zadaniem jest rozpoznanie encji tylko tych typów, które zostały włączone dla tego przebiegu analizy, i otagowanie ich w poniższym tekście łacińskiego, polskiego lub niemieckiego dokumentu historycznego.

DOZWOLONE TAGI W TYM ZADANIU:
{enabled_tag_labels}

TAGI WYŁĄCZONE:
{disabled_tag_labels}

ZASADY TAGOWANIA:
{tagging_rules}

Tekst do analizy:
---
{raw_text}
---

Zwróć TYLKO wynikowy kod XML. Nie dodawaj komentarzy ani wyjaśnień.
    """
    try:
        first_pass_xml = generate_tagged_xml_with_gemini(prompt, enabled_tag_types=enabled_tag_types)
        diagnostic_log("Pierwszy pass tagowania XML zakończony.")
        try:
            reviewed_xml = review_tagged_xml_with_gemini(
                raw_text,
                first_pass_xml,
                enabled_tag_types=enabled_tag_types,
            )
            diagnostic_log("Drugi pass korekcyjny tagowania XML zakończony.")
            return reviewed_xml
        except Exception as review_exc:
            diagnostic_log(f"Drugi pass korekcyjny nie powiódł się: {review_exc}")
            return first_pass_xml
    except Exception as exc:
        print(f"Błąd tagowania Gemini: {exc}")
        return None

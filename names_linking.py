import os
import re
import json
import requests
import time
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from dotenv import load_dotenv


load_dotenv()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")   
GEONAMES_USERNAME = os.environ.get("GEONAMES_USERNAME")
TEXT2NER_DIAGNOSTIC = os.environ.get("TEXT2NER_DIAGNOSTIC", "").strip().lower() in {"1", "true", "yes", "on"}
#MODEL = 'gemini-3-flash-preview'
MODEL = 'gemini-3.1-flash-lite-preview'
TIMEOUT_MS = 120 * 1000

client = genai.Client(api_key=GEMINI_API_KEY)


# -------------------------------- FUNCTIONS ----------------------------------
def get_json_response(url, params, headers, source_label):
    """Pobiera JSON z defensywną obsługą błędów HTTP i odpowiedzi nie-JSON."""
    response = requests.get(url, params=params, headers=headers, timeout=30)
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
    return re.sub(r'\s+', ' ', value).strip()


def diagnostic_log(message):
    if TEXT2NER_DIAGNOSTIC:
        print(f"[TEXT2NER-DIAG] {message}")


def tokenize_for_match(value):
    cleaned = re.sub(r"[^\w\s-]", " ", value.casefold(), flags=re.UNICODE)
    return [token for token in cleaned.split() if len(token) > 2]


def has_overlap_tokens(left, right):
    left_tokens = set(tokenize_for_match(left))
    right_tokens = set(tokenize_for_match(right))
    if not left_tokens or not right_tokens:
        return False
    return bool(left_tokens & right_tokens)


def common_prefix_length(left, right):
    left = normalize_whitespace(left).casefold()
    right = normalize_whitespace(right).casefold()
    length = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        length += 1
    return length


def looks_like_latin_place_form(value):
    tokens = tokenize_for_match(value)
    if not tokens:
        return False
    token = tokens[0]
    latin_suffixes = (
        'anus', 'ana', 'anum', 'ense', 'ensem', 'ensi', 'ensis', 'ensi',
        'ianus', 'iana', 'ianum', 'icus', 'ica', 'icum', 'ina', 'ino',
        'inus', 'inum', 'iorum', 'ior', 'ii', 'iae', 'iam', 'io', 'ium'
    )
    return token.endswith(latin_suffixes)


def can_keep_place_normalization(surface, normalized_best, analysis):
    confidence = analysis.get("confidence", "low")
    if confidence not in {"high", "medium"}:
        return False

    normalized_tokens = tokenize_for_match(normalized_best)
    if not normalized_tokens or len(normalized_tokens) > 3:
        return False

    if not looks_like_latin_place_form(surface):
        return False

    prefix_len = common_prefix_length(surface, normalized_best)
    if prefix_len >= 4:
        return True
    if confidence == "high" and prefix_len >= 2:
        return True

    for variant in analysis.get("variants", []) or []:
        if variant == surface:
            continue
        if has_overlap_tokens(variant, normalized_best):
            return True

    return False


def build_place_fallback_analysis(name, analysis, place_like_markers, person_like_markers):
    filtered_clues = [
        clue for clue in analysis.get("context_clues", [])
        if any(marker in clue.casefold() for marker in place_like_markers)
    ]

    variants = [name]
    seen_variants = {name.casefold()}
    for variant in analysis.get("variants", []) or []:
        variant = normalize_whitespace(str(variant))
        if len(variant) < 2:
            continue
        folded = variant.casefold()
        if folded in seen_variants:
            continue
        if any(marker in folded for marker in person_like_markers):
            continue
        if has_overlap_tokens(name, variant):
            variants.append(variant)
            seen_variants.add(folded)
            continue
        if can_keep_place_normalization(name, variant, analysis):
            variants.append(variant)
            seen_variants.add(folded)

    return {
        "surface": name,
        "entity_type": "place",
        "normalized_best": name,
        "confidence": "low",
        "variants": variants,
        "context_clues": filtered_clues
    }


def extract_text_claims(entity):
    """Wyciąga krótkie tekstowe wartości statementów do użycia w rankingu kandydatów."""
    snippets = []
    seen = set()
    for statements in entity.get('claims', {}).values():
        for statement in statements:
            mainsnak = statement.get('mainsnak', {})
            datavalue = mainsnak.get('datavalue')
            if not datavalue:
                continue
            if datavalue.get('type') != 'string':
                continue
            value = normalize_whitespace(str(datavalue.get('value', '')))
            if len(value) < 6:
                continue
            folded = value.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            snippets.append(value)
    return snippets[:8]


def build_wikibase_fallback_queries(query):
    """Buduje kilka uproszczonych wariantów zapytania dla wyszukiwania w Wikibase."""
    cleaned = normalize_whitespace(query)
    without_punctuation = re.sub(r'[^\w\s-]', ' ', cleaned, flags=re.UNICODE)
    without_punctuation = normalize_whitespace(without_punctuation)

    title_words = {
        'abp', 'arcybiskup', 'biskup', 'bp', 'cesarz', 'cardinalis', 'cardinal',
        'doktor', 'doctor', 'dr', 'dux', 'electus', 'graf', 'grave', 'han',
        'herre', 'hetman', 'imperator', 'kanclerz', 'kapłan', 'kardynał',
        'kardynal', 'king', 'król', 'krol', 'królowa', 'krolowa', 'książę',
        'ksiaze', 'landgraf', 'legat', 'magister', 'margrabia', 'opat',
        'papież', 'papiez', 'prałat', 'pralat', 'regina', 'rex', 'saint',
        'sanctus', 'sir', 'sułtan', 'sultan', 'techant', 'von', 'wojewoda'
    }

    variants = []
    seen = set()

    def add_variant(value):
        normalized = re.sub(r'\s+', ' ', value).strip(" ,.;:()[]{}\"'")
        if len(normalized) < 3:
            return
        lowered = normalized.casefold()
        if lowered in seen:
            return
        seen.add(lowered)
        variants.append(normalized)

    add_variant(cleaned)
    add_variant(without_punctuation)

    tokens = without_punctuation.split()
    tokens_wo_titles = [token for token in tokens if token.casefold() not in title_words]
    add_variant(" ".join(tokens_wo_titles))

    if len(tokens_wo_titles) >= 2:
        add_variant(" ".join(tokens_wo_titles[:2]))
        add_variant(" ".join(tokens_wo_titles[-2:]))

    for token in tokens_wo_titles:
        if len(token) >= 5:
            add_variant(token)

    return variants


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


def normalize_analysis_result(name, tag_type, analysis):
    """Normalizuje strukturę zwracaną przez model do przewidywalnego słownika."""
    fallback = {
        "surface": name,
        "entity_type": "place" if tag_type == "placeName" else "person",
        "normalized_best": name,
        "confidence": "low",
        "variants": [name],
        "context_clues": []
    }
    if not isinstance(analysis, dict):
        return fallback

    normalized_best = normalize_whitespace(str(analysis.get("normalized_best", "") or name))
    confidence = str(analysis.get("confidence", "low")).strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    variants = []
    seen_variants = set()
    for value in [name, normalized_best] + list(analysis.get("variants", []) or []):
        if not value:
            continue
        value = normalize_whitespace(str(value))
        if len(value) < 2:
            continue
        folded = value.casefold()
        if folded in seen_variants:
            continue
        seen_variants.add(folded)
        variants.append(value)

    context_clues = []
    seen_clues = set()
    for clue in analysis.get("context_clues", []) or []:
        clue = normalize_whitespace(str(clue))
        if len(clue) < 3:
            continue
        folded = clue.casefold()
        if folded in seen_clues:
            continue
        seen_clues.add(folded)
        context_clues.append(clue)

    return {
        "surface": name,
        "entity_type": "place" if tag_type == "placeName" else "person",
        "normalized_best": normalized_best or name,
        "confidence": confidence,
        "variants": variants or [name],
        "context_clues": context_clues
    }


def validate_entity_analysis(name, tag_type, analysis):
    """Chroni pipeline przed oczywiście błędną zmianą typu encji przez analizę modelu."""
    if tag_type != "placeName":
        return analysis

    normalized_best = analysis.get("normalized_best", name)
    place_like_markers = {
        'archidiecezja', 'civitas', 'diecezja', 'kraj', 'kraina', 'miasto',
        'miejscowość', 'panstwo', 'państwo', 'parafia', 'powiat', 'region',
        'rzeka', 'wieś', 'wies'
    }
    person_like_markers = {
        'arcybiskup', 'biskup', 'bp', 'cardinalis', 'collector', 'dux',
        'episcopus', 'hetman', 'kardynał', 'krol', 'król', 'książę',
        'nuncius', 'opat', 'papież', 'papiez', 'postać', 'rex', 'vir',
        'wojewoda'
    }

    normalized_folded = normalized_best.casefold()
    surface_folded = name.casefold()

    if any(marker in normalized_folded for marker in person_like_markers):
        diagnostic_log(
            f"Walidacja placeName '{name}': odrzucono normalized_best='{normalized_best}' "
            f"jako zbyt osobowe."
        )
        return build_place_fallback_analysis(name, analysis, place_like_markers, person_like_markers)

    if not has_overlap_tokens(name, normalized_best):
        if can_keep_place_normalization(name, normalized_best, analysis):
            diagnostic_log(
                f"Walidacja placeName '{name}': zachowano normalized_best='{normalized_best}' "
                f"mimo braku wspólnych tokenów, bo wygląda na poprawną łacińską formę miejscową."
            )
            return analysis
        diagnostic_log(
            f"Walidacja placeName '{name}': odrzucono normalized_best='{normalized_best}' "
            f"z powodu braku wspólnych tokenów."
        )
        return build_place_fallback_analysis(name, analysis, place_like_markers, person_like_markers)

    if surface_folded == normalized_folded:
        return analysis

    return analysis


def is_likely_entity_match(candidate_name, candidate_desc, tag_type):
    """Odrzuca ewidentnie nietrafione wyniki pełnotekstowe, np. dokumenty zamiast osób lub miejsc."""
    haystack = f"{candidate_name} {candidate_desc}".casefold()

    document_markers = {
        'akt', 'akta', 'bulla', 'dokument', 'document', 'dyplom', 'edycja',
        'epistola', 'formularz', 'karta', 'kopiariusz', 'korespondencja',
        'list', 'lista', 'metryka', 'nota', 'pokwitowanie', 'przywilej',
        'rachunek', 'regest', 'spis', 'testament', 'wiadomość', 'zapis'
    }
    place_markers = {
        'archidiecezja', 'civitas', 'diecezja', 'gród', 'grod', 'kraj',
        'kraina', 'miasto', 'miejscowość', 'miejscowosc', 'opactwo',
        'osada', 'państwo', 'panstwo', 'parafia', 'powiat', 'region',
        'rzeka', 'state', 'town', 'village', 'wieś', 'wies'
    }
    person_markers = {
        'abbas', 'arcybiskup', 'author', 'autor', 'biskup', 'bishop', 'bp',
        'cardinal', 'cardinalis', 'cesarz', 'collector', 'doctor', 'doktor',
        'duchess', 'dux', 'emperor', 'episcopus', 'hetman', 'imperator',
        'kanclerz', 'kapłan', 'kaplan', 'kardynał', 'kardynal', 'king',
        'król', 'krol', 'królowa', 'krolowa', 'książę', 'ksiaze', 'lord',
        'margrabia', 'monarcha', 'opat', 'papież', 'papiez', 'persona',
        'postać', 'postac', 'prince', 'princess', 'queen', 'regina', 'rex',
        'saint', 'sanctus', 'scholar', 'scribe', 'sekretarz', 'sultan',
        'sułtan', 'święty', 'swiety', 'techant', 'uczony', 'vir', 'władca',
        'wladca', 'wojewoda'
    }
    office_markers = {
        'archiepiscopus', 'biskup poznański', 'biskup krakowski', 'dioecesis',
        'ecclesia', 'episcopus', 'kanonik', 'kapituła', 'kapitula',
        'metropolita', 'nuncius', 'officialis', 'officium', 'ordynariusz',
        'parochus', 'poznań, episcopus', 'prepozyt', 'proboszcz', 'sede',
        'sedes', 'stolica biskupia', 'suffragan'
    }

    if any(marker in haystack for marker in document_markers):
        return False
    if tag_type == 'persName' and any(marker in haystack for marker in office_markers):
        return False
    if tag_type == 'persName' and any(marker in haystack for marker in place_markers):
        return False
    if tag_type == 'placeName' and any(marker in haystack for marker in person_markers | office_markers):
        return False
    return True


def enrich_wikibase_entities(ids, api_url, source_label, entity_base_url):
    """Pobiera etykiety i opisy dla znalezionych identyfikatorów encji."""
    if not ids:
        return []

    try:
        params = {
            "action": "wbgetentities",
            "ids": "|".join(ids),
            "languages": "pl|la|en",
            "format": "json",
            "props": "labels|descriptions|aliases|claims"
        }
        headers = {
            'User-Agent': 'EdycjaCyfrowa (PHC IHPAN) - skrypt badawczy'
        }
        response = get_json_response(api_url, params, headers, source_label)
        entities = response.get('entities', {})

        candidates = []
        for entity_id in ids:
            entity = entities.get(entity_id, {})
            labels = entity.get('labels', {})
            descriptions = entity.get('descriptions', {})
            aliases = entity.get('aliases', {})

            label = (
                labels.get('pl', {}).get('value')
                or labels.get('la', {}).get('value')
                or labels.get('en', {}).get('value')
                or entity_id
            )
            desc = (
                descriptions.get('pl', {}).get('value')
                or descriptions.get('la', {}).get('value')
                or descriptions.get('en', {}).get('value')
                or 'Brak opisu'
            )

            label_values = []
            for lang in ('pl', 'la', 'en'):
                value = labels.get(lang, {}).get('value')
                if value:
                    label_values.append(value)

            alias_values = []
            for lang in ('pl', 'la', 'en'):
                alias_values.extend(alias.get('value') for alias in aliases.get(lang, []))
            label_variants = ", ".join(dict.fromkeys(label_values)) if label_values else "Brak etykiet alternatywnych"
            aliases_str = ", ".join(dict.fromkeys(alias_values)) if alias_values else "Brak aliasów"
            claim_texts = extract_text_claims(entity)

            candidates.append({
                "id": entity_id,
                "name": label,
                "desc": f"{source_label}: {desc} | Etykiety: {label_variants} | Aliasy: {aliases_str}",
                "url": f"{entity_base_url}/{entity_id}",
                "source": source_label,
                "claim_texts": claim_texts
            })
        return candidates
    except Exception as e:
        print(f"Błąd pobierania encji {source_label} dla {ids}: {e}")
        return []


def search_wikibase_fulltext(query, api_url, source_label, entity_base_url, tag_type=None):
    """Fallback oparty o silnik Special:Search/CirrusSearch dla instancji Wikibase."""
    headers = {
        'User-Agent': 'EdycjaCyfrowa (PHC IHPAN) - skrypt badawczy'
    }

    try:
        for variant in build_wikibase_fallback_queries(query):
            params = {
                "action": "query",
                "list": "search",
                "srsearch": f"{variant}~2",
                "srnamespace": 120,
                "srlimit": 5,
                "format": "json"
            }
            response = get_json_response(api_url, params, headers, source_label)
            hits = response.get('query', {}).get('search', [])

            entity_ids = []
            for item in hits:
                title = item.get('title', '')
                match = re.fullmatch(r'Item:(Q\d+)', title)
                if match:
                    entity_ids.append(match.group(1))

            entity_ids = list(dict.fromkeys(entity_ids))
            if entity_ids:
                candidates = enrich_wikibase_entities(entity_ids, api_url, source_label, entity_base_url)
                if tag_type:
                    candidates = [
                        candidate for candidate in candidates
                        if is_likely_entity_match(candidate['name'], candidate['desc'], tag_type)
                    ]
                if candidates:
                    return candidates
        return []
    except Exception as e:
        print(f"Błąd pełnotekstowego wyszukiwania {source_label} dla {query}: {e}")
        return []


def search_wikibase_entities(query, api_url, source_label, entity_base_url=None, tag_type=None):
    """Pobiera kandydatów z instancji Wikibase, a przy braku trafień próbuje uproszczonych wariantów zapytania."""
    headers = {
        'User-Agent': 'EdycjaCyfrowa (PHC IHPAN) - skrypt badawczy'
    }

    candidates = []
    found_ids = []
    seen_ids = set()

    try:
        for variant in build_wikibase_fallback_queries(query):
            for language in ("pl", "la", "en"):
                params = {
                    "action": "wbsearchentities",
                    "search": variant,
                    "language": language,
                    "format": "json",
                    "limit": 5
                }
                response = get_json_response(api_url, params, headers, source_label)
                for item in response.get('search', []):
                    item_id = item['id']
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                    found_ids.append(item_id)

            if found_ids:
                candidates = enrich_wikibase_entities(found_ids, api_url, source_label, entity_base_url)
                break

        if tag_type:
            candidates = [
                candidate for candidate in candidates
                if is_likely_entity_match(candidate['name'], candidate['desc'], tag_type)
            ]

        if not candidates and source_label in {"WikiHum", "va.wiki.kul.pl"} and entity_base_url:
            candidates = search_wikibase_fulltext(query, api_url, source_label, entity_base_url, tag_type=tag_type)

        return candidates
    except Exception as e:
        print(f"Błąd {source_label} dla {query}: {e}")
        return []


def save_clean_xml(soup, output_file):
    xml_content = soup.prettify(formatter="minimal")

    tags_to_fix = ['persName', 'placeName']
    for tag in tags_to_fix:
        pattern = rf'<{tag}[^>]*>\s*(.*?)\s*</{tag}>'
        xml_content = re.sub(pattern, lambda m: re.sub(r'\s+', ' ', m.group(0)).replace('> ', '>').replace(' <', '<'), xml_content, flags=re.DOTALL)

        xml_content = re.sub(rf'<{tag}(.*?)>\s+', rf'<{tag}\1>', xml_content)
        xml_content = re.sub(rf'\s+</{tag}>', f'</{tag}>', xml_content)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(xml_content)


def tag_entities_with_gemini(raw_text):
    """
    Wykorzystuje Gemini do rozpoznania osób i miejsc w surowym tekście
    i otagowania ich zgodnie ze standardem TEI (persName, placeName).
    """
    prompt = f"""
Jesteś ekspertem od cyfrowej edycji tekstów historycznych, standardu TEI-XML i paleografem.
Twoim zadaniem jest rozpoznanie nazw osób (postaci historycznych) oraz nazw geograficznych (miejscowości, krain, rzek) w poniższym tekście łacińskiego dokumentu historycznego.

ZASADY TAGOWANIA:
1. Użyj znacznika <persName> dla osób. Zwróć szczególną uwagę na średniowieczne zapisy nazw gdzie czasem nie występują nazwiska 
   lecz zapis: Jan ze Żnina, Otto von Stamburg itp. - to są pełne nazwy osób, nie należy tagować osobno miejscowości lecz
   całość jako osobę: <persName>Jan ze Żnina</persName> 
2. Użyj znacznika <placeName> dla miejsc.
2a. Jeśli nazwa miejscowa występuje tylko jako przymiotnik lub element tytułu/urzędu osoby, nie taguj jej osobno jako <placeName>.
    Przykład: "episcopus Poznaniensis" to opis urzędu osoby, a nie samodzielne wskazanie miejsca do otagowania.
3. NIE zmieniaj ani jednego znaku w oryginalnym tekście (zachowaj pisownię, interpunkcję, wielkość liter).
4. Całość umieść wewnątrz tagu <div type="document">.
5. Uwzględnij podział tekstu na akapity, używając znacznika <p>.
6. Otaguj tylko te nazwy, które faktycznie występują w tekście.

Tekst do analizy:
---
{raw_text}
---

Zwróć TYLKO wynikowy kod XML. Nie dodawaj komentarzy ani wyjaśnień.
    """
    try:
        http_options = types.HttpOptions(timeout=TIMEOUT_MS)
        config = types.GenerateContentConfig(
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            http_options=http_options
        )

        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=config
        )

        # Oczyszczanie odpowiedzi z ewentualnych bloków markdown ```xml ... ```
        tagged_text = response.text.strip()
        tagged_text = re.sub(r'^```xml|```$', '', tagged_text).strip()

        # jeżeli brak tagu <div>
        if not tagged_text.startswith('<div'):
            tagged_text = f'<div type="document">{tagged_text}</div>'

        return tagged_text
    except Exception as e:
        print(f"Błąd tagowania Gemini: {e}")
        return None

def create_initial_tei(input_txt_file, output_xml_file):
    """Wczytuje tekst, taguje go przez Gemini i tworzy szkielet pliku TEI-XML."""
    with open(input_txt_file, 'r', encoding='utf-8') as f:
        content = f.read()

    print("Rozpoczynam automatyczne tagowanie tekstu (NER)...")
    tagged_content = tag_entities_with_gemini(content)

    if not tagged_content:
        print("Nie udało się otagować tekstu.")
        return False

    # Tworzenie minimalistycznego szkieletu TEI
    tei_template = f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
      <fileDesc>
         <titleStmt><title>Dokument z automatyczną identyfikacją encji</title></titleStmt>
         <publicationStmt><p>PHC IHPAN</p></publicationStmt>
         <sourceDesc><p>Dokument źródłowy</p></sourceDesc>
      </fileDesc>
  </teiHeader>
  <text>
      <body>
          <div>
            {tagged_content}
          </div>
      </body>
  </text>
</TEI>
"""
    with open(output_xml_file, 'w', encoding='utf-8') as f:
        f.write(tei_template)

    print(f"Wstępny plik XML przygotowany: {output_xml_file}")
    return True

def search_wikidata(query, tag_type=None):
    """pobieranie kandydatów z Wikidata Search API."""
    return search_wikibase_entities(
        query,
        api_url="https://www.wikidata.org/w/api.php",
        source_label="Wikidata",
        entity_base_url="https://www.wikidata.org/entity",
        tag_type=tag_type
    )


def search_wikihum(query, tag_type=None):
    """pobieranie kandydatów z WikiHum (instancja Wikibase)."""
    return search_wikibase_entities(
        query,
        api_url="https://wikihum.lab.dariah.pl/api.php",
        source_label="WikiHum",
        entity_base_url="https://wikihum.lab.dariah.pl/entity",
        tag_type=tag_type
    )


def search_va_wiki_kul(query, tag_type=None):
    """pobieranie kandydatów z va.wiki.kul.pl (instancja Wikibase)."""
    return search_wikibase_entities(
        query,
        api_url="https://va.wiki.kul.pl/w/api.php",
        source_label="va.wiki.kul.pl",
        entity_base_url="https://va.wiki.kul.pl/entity",
        tag_type=tag_type
    )


def search_geonames(query):
    """pobieranie kandydatów z GeoNames Search API."""
    url = "http://api.geonames.org/searchJSON"
    params = {
        "q": query,
        "maxRows": 5,
        "username": GEONAMES_USERNAME,
        "style": "SHORT" # zwraca najważniejsze dane (kraj, nazwa)
    }
    try:
        headers = {
            'User-Agent': 'EdycjaCyfrowa (PHC IHPAN) - skrypt badawczy'
        }
        response = get_json_response(url, params, headers, "GeoNames")
        candidates = []
        for item in response.get('geonames', []):
            country = item.get('countryCode', 'Nieznany kraj')
            fcodeName = item.get('fcodeName', '')
            candidates.append({
                "id": str(item['geonameId']),
                "name": item['name'],
                "desc": f"GeoNames: {fcodeName} w kraju {country}",
                "url": f"https://www.geonames.org/{item['geonameId']}",
                "source": "GeoNames"
            })
        return candidates
    except Exception as e:
        print(f"Błąd GeoNames dla {query}: {e}")
        return []
    

def analyze_name_with_gemini(name, context, tag_type):
    """Zwraca ostrożną analizę encji: nazwę bazową, warianty i wskazówki z kontekstu."""
    typ_encji = "miejscowość / region" if tag_type == "placeName" else "postać historyczna"

    prompt = f"""
Jesteś historykiem i filologiem klasycznym. Analizujesz dokument historyczny z przełomu XV i XVI wieku (Polska i kraje ościenne, łacina lub język niemiecki).
W tekście występuje {typ_encji} zapisana jako: "{name}".
Kontekst: "{context}"

Twoim zadaniem NIE jest zgadywanie na siłę pełnej tożsamości. Masz przygotować OSTROŻNĄ nazwę do dalszego wyszukiwania w bazach referencyjnych.

ZASADY:
1. Jeśli pewność nie jest wysoka, preferuj nazwę krótszą i bardziej ogólną.
2. Nie dopowiadaj przydomka, miejsca pochodzenia, rodu ani urzędu, jeśli nie wynikają jednoznacznie z kontekstu.
3. Jeśli widzisz cechy pomocne w identyfikacji, wypisz je osobno jako krótkie wskazówki kontekstowe.
4. Dla osób średniowiecznych forma bazowa może być mniej szczegółowa niż finalna identyfikacja.
5. Jeśli nie jesteś pewien pełnej identyfikacji, lepiej zwrócić np. "Dobrogost" niż błędnie "Dobrogost z Kurozwęk".
6. Zwróć tylko obiekt JSON bez komentarzy.

Zwróć dokładnie pola:
- "normalized_best": najlepsza ostrożna nazwa do wyszukiwania
- "confidence": jedno z "high", "medium", "low"
- "variants": lista 2-5 sensownych wariantów wyszukiwawczych, od ostrożnych do bardziej szczegółowych
- "context_clues": lista krótkich wskazówek z kontekstu, np. funkcji, urzędów, miejsc, relacji

Przykłady:
{{
  "normalized_best": "Toruń",
  "confidence": "high",
  "variants": ["Toruń", "Thorun", "Thorunii"],
  "context_clues": ["miasto pruskie"]
}}

{{
  "normalized_best": "Dobrogost",
  "confidence": "medium",
  "variants": ["Dobrogost", "Dobrogostius", "Dobrogost z Nowego Dworu"],
  "context_clues": ["biskup poznański", "kolektor papieski"]
}}

{{
  "normalized_best": "Lucca",
  "confidence": "high",
  "variants": ["Lucanus", "Lucca", "Luca"],
  "context_clues": ["miasto toskańskie", "forma łacińska przymiotnikowa od nazwy miejsca"]
}}
    """
    try:
        http_options = types.HttpOptions(timeout=TIMEOUT_MS)
        config = types.GenerateContentConfig(
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    http_options=http_options
                )
         
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=config
        )

        analysis = parse_json_object(response.text)
        normalized = normalize_analysis_result(name, tag_type, analysis)
        normalized = validate_entity_analysis(name, tag_type, normalized)
        diagnostic_log(
            f"Analiza encji '{name}' ({tag_type}): normalized_best='{normalized['normalized_best']}', "
            f"confidence={normalized['confidence']}, variants={normalized['variants']}, "
            f"context_clues={normalized['context_clues']}"
        )
        return normalized
    except Exception as e:
        print(f"Błąd analizy Gemini dla {name}: {e}")
        normalized = normalize_analysis_result(name, tag_type, None)
        normalized = validate_entity_analysis(name, tag_type, normalized)
        diagnostic_log(
            f"Fallback analizy encji '{name}' ({tag_type}) po błędzie: "
            f"normalized_best='{normalized['normalized_best']}', confidence={normalized['confidence']}"
        )
        return normalized


def normalize_name_with_gemini(name, context, tag_type):
    """Kompatybilny wrapper zwracający najlepszą nazwę bazową."""
    return analyze_name_with_gemini(name, context, tag_type)["normalized_best"]


def build_search_queries(entity_analysis, context, tag_type):
    """Buduje zestaw zapytań do baz referencyjnych na podstawie ostrożnej analizy i kontekstu."""
    queries = []
    seen = set()

    def add_query(value):
        value = normalize_whitespace(value)
        if len(value) < 2:
            return
        folded = value.casefold()
        if folded in seen:
            return
        seen.add(folded)
        queries.append(value)

    add_query(entity_analysis["surface"])
    add_query(entity_analysis["normalized_best"])

    for variant in entity_analysis.get("variants", []):
        add_query(variant)

    base_names = entity_analysis.get("variants", [])[:2] or [entity_analysis["normalized_best"]]
    clues = entity_analysis.get("context_clues", [])[:3]

    if entity_analysis.get("confidence") == "high":
        for clue in clues[:2]:
            add_query(f"{entity_analysis['normalized_best']} {clue}")
    else:
        for base_name in base_names[:2]:
            add_query(base_name)
            for clue in clues[:2]:
                add_query(f"{base_name} {clue}")

    # Wspierający fallback z samego kontekstu tylko dla osób.
    context_tokens = tokenize_for_match(context)
    if tag_type == "persName" and len(context_tokens) >= 2:
        add_query(" ".join(context_tokens[:3]))

    return queries[:8]


def score_candidate(candidate, entity_analysis, context, tag_type):
    """Nadaje kandydatom prosty wynik zgodności z nazwą i kontekstem."""
    score = 0
    haystack = f"{candidate.get('name', '')} {candidate.get('desc', '')}".casefold()
    claim_haystack = " ".join(candidate.get("claim_texts", [])).casefold()

    name_tokens = set(tokenize_for_match(entity_analysis["normalized_best"]))
    surface_tokens = set(tokenize_for_match(entity_analysis["surface"]))
    variant_tokens = set()
    for variant in entity_analysis.get("variants", []):
        variant_tokens.update(tokenize_for_match(variant))

    clue_tokens = set()
    for clue in entity_analysis.get("context_clues", []):
        clue_tokens.update(tokenize_for_match(clue))

    for token in surface_tokens:
        if token in haystack:
            score += 4
        if token in claim_haystack:
            score += 5
    for token in name_tokens:
        if token in haystack:
            score += 5
        if token in claim_haystack:
            score += 7
    for token in variant_tokens:
        if token in haystack:
            score += 2
        if token in claim_haystack:
            score += 3
    for token in clue_tokens:
        if token in haystack:
            score += 3
        if token in claim_haystack:
            score += 5

    if candidate.get("source") == "GeoNames" and tag_type == "placeName":
        score += 4
    if candidate.get("source") in {"WikiHum", "va.wiki.kul.pl"} and tag_type == "persName":
        score += 2

    if not is_likely_entity_match(candidate.get("name", ""), candidate.get("desc", ""), tag_type):
        score -= 8

    if entity_analysis.get("confidence") == "high":
        score += 1
    elif entity_analysis.get("confidence") == "low":
        score -= 1

    return score


def collect_candidates(entity_analysis, context, tag_type):
    """Wyszukuje kandydatów dla kilku wariantów nazwy i sortuje ich po prostym rankingu."""
    queries = build_search_queries(entity_analysis, context, tag_type)
    diagnostic_log(
        f"Zapytania dla '{entity_analysis['surface']}' ({tag_type}): {queries}"
    )
    collected = {}

    for query in queries:
        if tag_type == 'placeName':
            query_candidates = (
                search_geonames(query) +
                search_va_wiki_kul(query, tag_type=tag_type) +
                search_wikidata(query, tag_type=tag_type)
            )
        else:
            query_candidates = (
                search_wikihum(query, tag_type=tag_type) +
                search_va_wiki_kul(query, tag_type=tag_type) +
                search_wikidata(query, tag_type=tag_type)
            )

        query_result_labels = [
            f"{candidate.get('source', '?')}:{candidate.get('id')}"
            for candidate in query_candidates
        ]
        diagnostic_log(
            f"Wyniki dla zapytania '{query}' ({tag_type}): "
            f"{query_result_labels}"
        )

        for candidate in query_candidates:
            candidate_key = candidate.get("url") or f"{candidate.get('source')}:{candidate.get('id')}"
            candidate_copy = dict(candidate)
            candidate_copy.setdefault("matched_queries", [])
            if candidate_key in collected:
                existing = collected[candidate_key]
                if query not in existing["matched_queries"]:
                    existing["matched_queries"].append(query)
                    existing["score"] += 2
                existing["score"] += score_candidate(existing, entity_analysis, context, tag_type)
            else:
                candidate_copy["matched_queries"] = [query]
                candidate_copy["score"] = score_candidate(candidate_copy, entity_analysis, context, tag_type)
                collected[candidate_key] = candidate_copy

    ranked = sorted(
        collected.values(),
        key=lambda candidate: (candidate.get("score", 0), candidate.get("name", "")),
        reverse=True
    )
    diagnostic_log(
        f"Ranking kandydatów dla '{entity_analysis['surface']}' ({tag_type}): " +
        "; ".join(
            f"{candidate['name']} [{candidate.get('source', '?')}:{candidate['id']}] "
            f"score={candidate.get('score', 0)} queries={candidate.get('matched_queries', [])}"
            for candidate in ranked[:8]
        )
    )
    return ranked[:8]


def ask_gemini_to_disambiguate(name, name_n, context, candidates, entity_analysis=None):
    """ wysyłanie zapytanie do Gemini z prośbą o wybór właściwego ID z listy przedstawionych kandydatów """
    if not candidates:
        return None

    if len(candidates) == 1:
        selected_url = candidates[0].get("url")
        diagnostic_log(
            f"Disambiguation skrócone dla '{name}' ({name_n}) - jedyny kandydat: {selected_url}"
        )
        return selected_url

    top_candidate = candidates[0]
    second_score = candidates[1].get("score", -999) if len(candidates) > 1 else -999
    if top_candidate.get("score", 0) >= 60 and top_candidate.get("score", 0) - second_score >= 25:
        selected_url = top_candidate.get("url")
        diagnostic_log(
            f"Disambiguation skrócone dla '{name}' ({name_n}) - dominujący kandydat: "
            f"{selected_url} score={top_candidate.get('score', 0)} vs next={second_score}"
        )
        return selected_url

    candidates_text = ""
    for idx, c in enumerate(candidates):
        matched_queries = ", ".join(c.get("matched_queries", [])[:3]) or "brak"
        score = c.get("score", 0)
        candidates_text += f"- Opcja {idx+1}: ID: {c['id']} | Nazwa: {c['name']} | Opis: {c['desc']} | Score: {score} | Zapytania: {matched_queries} | URL: {c['url']}\n"

    analysis_text = ""
    if entity_analysis:
        clues = ", ".join(entity_analysis.get("context_clues", [])) or "brak"
        variants = ", ".join(entity_analysis.get("variants", [])[:5]) or entity_analysis.get("normalized_best", name_n)
        analysis_text = f"""
Ostrożna analiza nazwy:
- nazwa bazowa: "{entity_analysis.get('normalized_best', name_n)}"
- pewność: "{entity_analysis.get('confidence', 'low')}"
- warianty wyszukiwawcze: {variants}
- wskazówki z kontekstu: {clues}
"""

    prompt = f"""
Zadaniem jest tzw. Entity Linking (rozpoznawanie jednostek) w historycznym tekście (Polska i kraje ościenne, ok. 1501 roku).
Znaleziono encję o nazwie: "{name} ({name_n})".
Kontekst zdania, w którym występuje: "{context}".

{analysis_text}

Oto lista kandydatów pobranych z baz danych (Wikidata, WikiHum, GeoNames):
{candidates_text}

Przeanalizuj kontekst historyczny i gramatyczny (nazwa może być odmieniona po łacinie).
Preferuj kandydatów zgodnych z funkcjami, urzędami i relacjami z kontekstu. Odrzucaj dokumenty, jeśli encja ma być osobą lub miejscem.
Jeśli pierwszy kandydat ma wyraźnie najwyższy wynik punktowy i dobrze odpowiada nazwie oraz kontekstowi, wybierz go zamiast odpowiadać NONE.
Która opcja jest poprawna? 
Zwróć TYLKO I WYŁĄCZNIE pełny URL wybranego kandydata (np. https://www.wikidata.org/wiki/Q454521). 
Jeśli żaden kandydat nie pasuje do kontekstu, zwróć dokładnie słowo: NONE.
    """
    
    try:
        http_options = types.HttpOptions(timeout=TIMEOUT_MS)
        config = types.GenerateContentConfig(
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    http_options=http_options
                )
         
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=config
        )

        result = response.text.strip()
        if "NONE" in result.upper():
            diagnostic_log(
                f"Disambiguation dla '{name}' ({name_n}) zwróciło NONE. "
                f"Top kandydaci: {[candidate.get('url') for candidate in candidates[:5]]}"
            )
            return None
        diagnostic_log(
            f"Disambiguation dla '{name}' ({name_n}) wybrało: {result}"
        )
        return result
    except Exception as e:
        print(f"Błąd Gemini przy ewaluacji: {e}")
        return None


def process_tei_xml(input_file, output_file):
    """ procedura przetwarzania pliku tei xml """
    with open(input_file, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'xml')

    tags_to_process = soup.find_all(['persName', 'placeName'])
    
    for tag in tags_to_process:
        if tag.has_attr('ref'):
            continue
            
        name = tag.get_text()
        tag_type = tag.name # 'persName' lub 'placeName'

        parent_block = tag.find_parent(['p', 'ab', 'note', 'head'])
        
        if parent_block:
            raw_text = parent_block.get_text()
            # zamana wielokrotnych spacji, tabulatorów i znaków nowej linii na jedną spację
            context = re.sub(r'\s+', ' ', raw_text).strip()
        else:
            context = name

        print(f"\nPrzetwarzam: {name} ({tag_type}) dla kontekstu: {context}")

        entity_analysis = analyze_name_with_gemini(name, context, tag_type)
        normalized_name = entity_analysis["normalized_best"]
        print(f" Znormalizowano do: {normalized_name} (pewność: {entity_analysis['confidence']})")
        
        candidates = collect_candidates(entity_analysis, context, tag_type)

        selected_url = ask_gemini_to_disambiguate(name, normalized_name, context, candidates, entity_analysis=entity_analysis)
        
        tag['key'] = normalized_name
        if selected_url and selected_url.startswith("http"):
            tag['ref'] = selected_url
            print(f" -> Przypisano: {selected_url}")
            # zapis cząstkowy pliku xml
            save_clean_xml(soup, output_file)
        else:
            print(" -> Brak dopasowania (Gemini zwrócił NONE).")
            
        time.sleep(2) 

    # końcowy zapis zmodyfikowanego XML
    save_clean_xml(soup, output_file)
    print(f"\nZakończono! Zapisano plik: {output_file}")


# -------------------------------- MAIN ---------------------------------------
if __name__ == "__main__":

    input_txt = "document.txt"
    initial_xml = "tmp.xml"
    final_xml = "document.xml"

    if create_initial_tei(input_txt, initial_xml):
        process_tei_xml(initial_xml, final_xml)

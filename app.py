import os
import re
from functools import wraps
from flask import Flask, render_template, request, jsonify, Response
from werkzeug.middleware.proxy_fix import ProxyFix
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from dotenv import load_dotenv
from names_linking import (
    DEFAULT_ENABLED_TAG_TYPES,
    DEFAULT_GEMINI_MODEL,
    diagnostic_log,
    extract_years_from_text,
    link_entity,
    normalize_gemini_model_name,
    normalize_enabled_tag_types,
    normalize_whitespace,
    reset_current_gemini_model,
    set_current_gemini_model,
    start_diagnostic_session,
    stop_diagnostic_session,
    tag_entities_with_gemini,
)


load_dotenv()
app = Flask(__name__)
app.wsgi_app = ProxyFix(
    app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
APP_PASSWORD = os.environ.get('APP_PASSWORD')
APP_USER = os.environ.get('APP_USER')

client = genai.Client(api_key=GEMINI_API_KEY)

# szablon nagłówka dokumentu TEI
TEI_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt> <title>Dokument z automatyczną identyfikacją encji</title> </titleStmt>
      <publicationStmt> <p>PHC IHPAN</p> </publicationStmt>
      <sourceDesc> <p>Dokument źródłowy</p> </sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
  <body>
"""

TEI_FOOTER = """
    </body>
  </text>
</TEI>"""


# -------------------------------- FUNCTIONS ----------------------------------
def compress_inline_tags(xml_text):
    """Usuwa zbędne odstępy wewnątrz krótkich tagów inline TEI."""
    return re.sub(
        r'<(persName|placeName|orgName|date|roleName)(.*?)>\s*(.*?)\s*</\1>',
        r'<\1\2>\3</\1>',
        xml_text,
        flags=re.DOTALL,
    )


def serialize_inner_xml(soup):
    """Serializuje roboczy XML dokumentu do postaci gotowej do osadzenia w TEI."""
    inner_xml = soup.prettify(formatter="minimal")
    inner_xml = compress_inline_tags(inner_xml)
    inner_xml = re.sub(r'<\?xml.*?\?>', '', inner_xml).strip()
    return inner_xml


def build_full_tei_xml(inner_xml):
    """Składa pełny dokument TEI z wewnętrznej treści body."""
    return f"{TEI_HEADER}\n{inner_xml}\n{TEI_FOOTER}"


def extract_inner_xml_from_payload(xml_payload):
    """Wyciąga zawartość sekcji body z pełnego TEI albo zwraca podany XML roboczy."""
    xml_payload = str(xml_payload or "").strip()
    if not xml_payload:
        raise ValueError("Brak XML do identyfikacji.")

    if "<TEI" not in xml_payload:
        return xml_payload

    body_match = re.search(r"<body>\s*(.*?)\s*</body>", xml_payload, flags=re.DOTALL)
    if not body_match:
        raise ValueError("Nie udało się odnaleźć sekcji <body> w przesłanym TEI-XML.")
    return body_match.group(1).strip()


def check_auth(username, password):
    """Sprawdza, czy nazwa użytkownika i hasło są poprawne."""
    return username == APP_USER and password == APP_PASSWORD

def authenticate():
    """Wysyła odpowiedź 401, która wyzwala okno logowania w przeglądarce."""
    return Response(
        'Proszę się zalogować.\n'
        'Dostęp tylko dla pracowników IH PAN.', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'}
    )

def requires_auth(f):
    """Opakowuje widok Flaska prostym uwierzytelnianiem HTTP Basic."""
    @wraps(f)
    def decorated(*args, **kwargs):
        """Przepuszcza żądanie tylko dla poprawnych danych logowania."""
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


def get_entity_context(tag):
    """Zwraca możliwie pełny kontekst encji z najbliższego bloku tekstu."""
    parent_block = tag.find_parent(['p', 'ab', 'note', 'head'])
    if parent_block:
        raw_text = parent_block.get_text()
    else:
        raw_text = tag.parent.get_text() if tag.parent else tag.get_text()
    return re.sub(r'\s+', ' ', raw_text).strip()


def identify_entities_in_soup(soup, document_years):
    """Wykonuje linking dla `persName` i `placeName` na już otagowanym XML-u."""
    entities = []
    unresolved_entities = []
    seen_entities = set()
    seen_unresolved_entities = set()
    resolved_entity_cache = {}

    tags = soup.find_all(['persName', 'placeName'])
    for tag in tags:
        name = tag.get_text()
        tag_type = tag.name
        if tag.has_attr('key'):
            del tag['key']
        if tag.has_attr('ref'):
            del tag['ref']

        context = get_entity_context(tag)
        cache_key = (tag_type, normalize_whitespace(name).casefold())
        if cache_key in resolved_entity_cache:
            link_result = resolved_entity_cache[cache_key]
            diagnostic_log(
                f"Użyto cache dla encji '{name}' ({tag_type}) -> "
                f"{link_result['decision'].get('selected_url')}"
            )
        else:
            link_result = link_entity(
                name,
                context,
                tag_type,
                document_years=document_years,
            )
            if link_result["decision"].get("status") == "selected":
                resolved_entity_cache[cache_key] = link_result

        entity_analysis = link_result["entity_analysis"]
        norm_name = link_result["normalized_name"]
        decision = link_result["decision"]
        tag['key'] = norm_name

        selected_url = decision.get("selected_url")

        if decision.get("status") == "selected" and selected_url:
            tag['ref'] = selected_url
            entity_key = (norm_name, selected_url)
            if entity_key not in seen_entities:
                entities.append({
                    "name": norm_name,
                    "surface": name,
                    "type": tag_type,
                    "url": selected_url,
                })
                seen_entities.add(entity_key)
        else:
            unresolved_key = (tag_type, norm_name, normalize_whitespace(name))
            if unresolved_key not in seen_unresolved_entities:
                unresolved_entities.append({
                    "name": norm_name,
                    "surface": name,
                    "type": tag_type,
                    "reason": decision.get("reason", "not_selected"),
                })
                seen_unresolved_entities.add(unresolved_key)

    entities.sort(key=lambda x: x['name'])
    unresolved_entities.sort(key=lambda x: (x['type'], x['name'], x['surface']))
    return entities, unresolved_entities


def recognize_text_to_tei(raw_text, enabled_tag_types=None):
    """Rozpoznaje encje i zwraca pełny TEI-XML bez identyfikacji referencyjnej."""
    tagged_xml = tag_entities_with_gemini(raw_text, enabled_tag_types=enabled_tag_types)
    if tagged_xml is None:
        raise ValueError("Błąd tagowania tekstu przez model Gemini. Sprawdź połączenie lub klucz API.")

    soup = BeautifulSoup(tagged_xml, 'xml')
    inner_xml = serialize_inner_xml(soup)
    return build_full_tei_xml(inner_xml)


def identify_entities_in_tei(xml_payload):
    """Identyfikuje encje w dostarczonym TEI-XML lub XML-u roboczym."""
    inner_xml = extract_inner_xml_from_payload(xml_payload)
    soup = BeautifulSoup(inner_xml, 'xml')
    document_years = extract_years_from_text(soup.get_text(" "))
    diagnostic_log(f"Wykryte lata dokumentu dla identyfikacji: {document_years}")

    entities, unresolved_entities = identify_entities_in_soup(soup, document_years)
    normalized_inner_xml = serialize_inner_xml(soup)
    full_tei_xml = build_full_tei_xml(normalized_inner_xml)
    return full_tei_xml, entities, unresolved_entities


# -------------------------------- ROUTES -------------------------------------
@app.route('/')
@requires_auth
def index():
    """Renderuje główny interfejs aplikacji."""
    return render_template('index.html')

@app.route('/recognize', methods=['POST'])
@requires_auth
def recognize():
    """Rozpoznaje encje i zwraca TEI-XML bez identyfikacji referencyjnej."""
    diagnostic_log_path = None
    model_token = None
    try:
      diagnostic_log_path = start_diagnostic_session(log_dir="log")
      raw_text = request.json.get('text', '')[:5000]
      enabled_tag_types = normalize_enabled_tag_types(
          request.json.get('tag_types', list(DEFAULT_ENABLED_TAG_TYPES))
      )
      selected_model = normalize_gemini_model_name(
          request.json.get('model_name', DEFAULT_GEMINI_MODEL)
      )
      model_token = set_current_gemini_model(selected_model)
      if not enabled_tag_types:
          raise ValueError("Wybierz co najmniej jeden typ tagu do rozpoznawania encji.")
      diagnostic_log(f"Uruchomiono /recognize dla tekstu o długości {len(raw_text)} znaków.")
      diagnostic_log(f"Włączone tagi rozpoznawania: {enabled_tag_types}")
      diagnostic_log(f"Model Gemini dla /recognize: {selected_model}")
      full_tei_xml = recognize_text_to_tei(raw_text, enabled_tag_types=enabled_tag_types)

      return jsonify({
          "xml": full_tei_xml,
          "entities": [],
          "unresolved_entities": [],
          "identification_performed": False,
          "model_name": selected_model,
          "recognized_tag_types": enabled_tag_types,
          "diagnostic_log_file": diagnostic_log_path
      })
    
    except Exception as e:
        diagnostic_log(f"Błąd krytyczny w /recognize: {e}")
        print(f"Błąd krytyczny w /recognize: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        reset_current_gemini_model(model_token)
        stop_diagnostic_session()


@app.route('/identify', methods=['POST'])
@requires_auth
def identify():
    """Identyfikuje `persName` i `placeName` w istniejącym TEI-XML."""
    diagnostic_log_path = None
    model_token = None
    try:
      diagnostic_log_path = start_diagnostic_session(log_dir="log")
      xml_payload = request.json.get('xml', '')
      selected_model = normalize_gemini_model_name(
          request.json.get('model_name', DEFAULT_GEMINI_MODEL)
      )
      model_token = set_current_gemini_model(selected_model)
      diagnostic_log(f"Uruchomiono /identify dla XML o długości {len(xml_payload)} znaków.")
      diagnostic_log(f"Model Gemini dla /identify: {selected_model}")

      full_tei_xml, entities, unresolved_entities = identify_entities_in_tei(xml_payload)

      return jsonify({
          "xml": full_tei_xml,
          "entities": entities,
          "unresolved_entities": unresolved_entities,
          "identification_performed": True,
          "model_name": selected_model,
          "diagnostic_log_file": diagnostic_log_path
      })

    except Exception as e:
        diagnostic_log(f"Błąd krytyczny w /identify: {e}")
        print(f"Błąd krytyczny w /identify: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        reset_current_gemini_model(model_token)
        stop_diagnostic_session()


@app.route('/process', methods=['POST'])
@requires_auth
def process():
    """Zachowuje zgodność wsteczną: rozpoznaje i od razu identyfikuje encje."""
    diagnostic_log_path = None
    model_token = None
    try:
      diagnostic_log_path = start_diagnostic_session(log_dir="log")
      raw_text = request.json.get('text', '')[:5000]
      enabled_tag_types = normalize_enabled_tag_types(
          request.json.get('tag_types', list(DEFAULT_ENABLED_TAG_TYPES))
      )
      selected_model = normalize_gemini_model_name(
          request.json.get('model_name', DEFAULT_GEMINI_MODEL)
      )
      model_token = set_current_gemini_model(selected_model)
      if not enabled_tag_types:
          raise ValueError("Wybierz co najmniej jeden typ tagu do rozpoznawania encji.")
      diagnostic_log(f"Uruchomiono /process dla tekstu o długości {len(raw_text)} znaków.")
      diagnostic_log(f"Włączone tagi rozpoznawania w /process: {enabled_tag_types}")
      diagnostic_log(f"Model Gemini dla /process: {selected_model}")

      full_tei_xml = recognize_text_to_tei(raw_text, enabled_tag_types=enabled_tag_types)
      full_tei_xml, entities, unresolved_entities = identify_entities_in_tei(full_tei_xml)

      return jsonify({
          "xml": full_tei_xml,
          "entities": entities,
          "unresolved_entities": unresolved_entities,
          "identification_performed": True,
          "model_name": selected_model,
          "diagnostic_log_file": diagnostic_log_path
      })

    except Exception as e:
        diagnostic_log(f"Błąd krytyczny w /process: {e}")
        print(f"Błąd krytyczny w /process: {e}")
        return jsonify({"error": str(e)}), 500
    finally:
        reset_current_gemini_model(model_token)
        stop_diagnostic_session()


# -------------------------------- MAIN ---------------------------------------
if __name__ == '__main__':
    app.run(debug=True)

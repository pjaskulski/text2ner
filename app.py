import os
import re
from functools import wraps
from flask import Flask, render_template, request, jsonify, Response
from werkzeug.middleware.proxy_fix import ProxyFix
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from dotenv import load_dotenv
from names_linking import analyze_name_with_gemini, collect_candidates, ask_gemini_to_disambiguate, tag_entities_with_gemini


load_dotenv()
app = Flask(__name__)
app.wsgi_app = ProxyFix(
    app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
APP_PASSWORD = os.environ.get('APP_PASSWORD')
APP_USER = os.environ.get('APP_USER')

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL = 'gemini-3.1-flash-lite-preview'

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
    @wraps(f)
    def decorated(*args, **kwargs):
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


# -------------------------------- ROUTES -------------------------------------
@app.route('/')
@requires_auth
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
@requires_auth
def process():
    try:
      raw_text = request.json.get('text', '')[:5000]

      # NER Tagging
      tagged_xml = tag_entities_with_gemini(raw_text)
      if tagged_xml is None:
          return jsonify({"error": "Błąd tagowania tekstu przez model Gemini. Sprawdź połączenie lub klucz API."}), 500
      
      soup = BeautifulSoup(tagged_xml, 'xml')

      entities = []
      seen_entities = set()  # zbiór do śledzenia unikalnych linków

      # Entity Linking
      tags = soup.find_all(['persName', 'placeName'])
      for tag in tags:
          name = tag.get_text()
          tag_type = tag.name

          context = get_entity_context(tag)

          entity_analysis = analyze_name_with_gemini(name, context, tag_type)
          norm_name = entity_analysis["normalized_best"]
          tag['key'] = norm_name

          candidates = collect_candidates(entity_analysis, context, tag_type)

          selected_url = ask_gemini_to_disambiguate(name, norm_name, context, candidates, entity_analysis=entity_analysis)

          if selected_url:
              tag['ref'] = selected_url

              # KLUCZ UNIKALNOŚCI: Nazwa + URL
              entity_key = (norm_name, selected_url)
              if entity_key not in seen_entities:
                  entities.append({"name": norm_name,
                                 "surface": name,
                                 "type": tag_type,
                                 "url": selected_url
                  })
                  seen_entities.add(entity_key)

      inner_xml = soup.prettify(formatter="minimal")
      inner_xml = re.sub(r'<(persName|placeName)(.*?)>\s*(.*?)\s*</\1>', r'<\1\2>\3</\1>', inner_xml, flags=re.DOTALL)
      inner_xml = re.sub(r'<\?xml.*?\?>', '', inner_xml).strip()

      full_tei_xml = f"{TEI_HEADER}\n{inner_xml}\n{TEI_FOOTER}"

      entities.sort(key=lambda x: x['name'])

      return jsonify({
          "xml": full_tei_xml,
          "entities": entities
      })
    
    except Exception as e:
        print(f"Błąd krytyczny w /process: {e}")
        return jsonify({"error": str(e)}), 500


# -------------------------------- MAIN ---------------------------------------
if __name__ == '__main__':
    app.run(debug=True)

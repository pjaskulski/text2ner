import os
import re
import requests
import time
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from dotenv import load_dotenv


load_dotenv()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")   
GEONAMES_USERNAME = os.environ.get("GEONAMES_USERNAME")
#MODEL = 'gemini-3-flash-preview'
MODEL = 'gemini-3.1-flash-lite-preview'
TIMEOUT_MS = 120 * 1000

client = genai.Client(api_key=GEMINI_API_KEY)


# -------------------------------- FUNCTIONS ----------------------------------
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

def search_wikidata(query):
    """pobieranie kandydatów z Wikidata Search API."""
    url = "https://www.wikidata.org/w/api.php"
    params = {
        "action": "wbsearchentities",
        "search": query,
        "language": "pl",
        "format": "json",
        "limit": 5
    }

    headers = {
        'User-Agent': 'EdycjaCyfrowa (PHC IHPAN) - skrypt badawczy'
    }

    try:
        response = requests.get(url, params=params, headers=headers).json()
        candidates = []
        for item in response.get('search', []):
            desc = item.get('description', 'Brak opisu')
            aliases = item.get('aliases', [])
            aliases_str = ", ".join(aliases) if aliases else "Brak aliasów"
            candidates.append({
                "id": item['id'],
                "name": item['label'],
                "desc": f"Wikidata: {desc} | Aliasy: {aliases_str}",
                "url": item['concepturi']
            })
        return candidates
    except Exception as e:
        print(f"Błąd Wikidata dla {query}: {e}")
        return []


def search_wikihum(query):
    """pobieranie kandydatów z WikiHum (instancja Wikibase)."""
    url = "https://wikihum.lab.dariah.pl/api.php" 
    params = {
        "action": "wbsearchentities",
        "search": query,
        "language": "pl",
        "format": "json",
        "limit": 5
    }
    try:
        response = requests.get(url, params=params).json()
        candidates = []
        for item in response.get('search', []):
            desc = item.get('description', 'Brak opisu')
            aliases = item.get('aliases', [])
            aliases_str = ", ".join(aliases) if aliases else "Brak aliasów"
            candidates.append({
                "id": item['id'],
                "name": item['label'],
                "desc": f"WikiHum: {desc} | Aliasy: {aliases_str}",
                "url": f"https://wikihum.lab.dariah.pl/entity/{item['id']}"
            })
        return candidates
    except Exception as e:
        print(f"Błąd WikiHum dla {query}: {e}")
        return []


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
        response = requests.get(url, params=params).json()
        candidates = []
        for item in response.get('geonames', []):
            country = item.get('countryCode', 'Nieznany kraj')
            fcodeName = item.get('fcodeName', '')
            candidates.append({
                "id": str(item['geonameId']),
                "name": item['name'],
                "desc": f"GeoNames: {fcodeName} w kraju {country}",
                "url": f"https://www.geonames.org/{item['geonameId']}"
            })
        return candidates
    except Exception as e:
        print(f"Błąd GeoNames dla {query}: {e}")
        return []
    

def normalize_name_with_gemini(name, context, tag_type):
    """Gemini korzystajc z kontekstu wystąpienia nazwy sprowadza nazwę łacińską / niemiecką do polskiego mianownika,
       ustalając jeżeli to możliwe pełną identyfikację Np. Fridericus = Fryderyk Jagiellończyk, jeżeli to wynika z kontekstu.
       Korzysta przy tym ze swojej 'wiedzy' historycznej co bywa przydatne ale może też być niebezpieczne.
    """

    typ_encji = "miejscowość / region" if tag_type == "placeName" else "postać historyczna"
    
    prompt = f"""
Jesteś historykiem i filologiem klasycznym. W historycznym dokumencie z początku XVI wieku (Polska i kraje ościenne, łacina, lub jezyk niemiecki) znajduje się {typ_encji} o nazwie: "{name}".
Kontekst zdania: "{context}"

Twoim zadaniem jest podanie współczesnej, polskiej nazwy tej encji w mianowniku, która najlepiej nada się do wyszukiwarki encyklopedycznej. Rozważ kontekst w którym występuje nazwa.
Przykłady:
- "Thorunii" -> "Toruń"
- "Cracoviam" -> "Kraków"
- "Premislaus dux Opaviensis" -> "książę Przemysław opawski"
- "Sigismundus" -> "Zygmunt I Stary"

Zwróć TYLKO I WYŁĄCZNIE znormalizowaną, polską nazwę w mianowniku. 
ZAKAZ używania jakichkolwiek słów wstępu, wyjaśnień, znaków interpunkcyjnych na końcu czy formatowania (np. pogrubień).
Jeśli to osoba, podaj Imię i Przydomek/Nazwisko (np. Jan I Olbracht).
Jeśli nie wiesz lub nie jesteś pewien, zwróć dokładnie tą samą nazwę, którą przekazano do analizy: {name}
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
        
        normalized = response.text.strip()
        normalized = re.sub(r'^[\'"]|[\'".!?]+$', '', normalized).strip()
        if len(normalized) > 75 or '\n' in normalized:
            print(f" [Ostrzeżenie] Odpowiedź za długa lub wielolinijkowa ('{normalized[:20]}...'). Wracam do oryginału.")
            return name
        
        forbidden_phrases = ["oto", "odpowiedź", "to jest", "nazwa", "mianownik", "znormalizowana", "oczywiście"]
        normalized_lower = normalized.lower()
        if any(phrase in normalized_lower for phrase in forbidden_phrases):
            print(f" [Ostrzeżenie] Wykryto słowa konwersacyjne ('{normalized}'). Wracam do oryginału.")
            return name
        
        return normalized
    except Exception as e:
        print(f"Błąd lematyzacji Gemini dla {name}: {e}")
        return name 


def ask_gemini_to_disambiguate(name, name_n, context, candidates):
    """ wysyłanie zapytanie do Gemini z prośbą o wybór właściwego ID z listy przedstawionych kandydatów """
    if not candidates:
        return None

    candidates_text = ""
    for idx, c in enumerate(candidates):
        candidates_text += f"- Opcja {idx+1}: ID: {c['id']} | Nazwa: {c['name']} | Opis: {c['desc']} | URL: {c['url']}\n"

    prompt = f"""
Zadaniem jest tzw. Entity Linking (rozpoznawanie jednostek) w historycznym tekście (Polska i kraje ościenne, ok. 1501 roku).
Znaleziono encję o nazwie: "{name} ({name_n})".
Kontekst zdania, w którym występuje: "{context}".

Oto lista kandydatów pobranych z baz danych (Wikidata, WikiHum, GeoNames):
{candidates_text}

Przeanalizuj kontekst historyczny i gramatyczny (nazwa może być odmieniona po łacinie).
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
            return None
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

        normalized_name = normalize_name_with_gemini(name, context, tag_type)
        print(f" Znormalizowano do: {normalized_name}")
        
        candidates = []
        if tag_type == 'placeName':
            candidates.extend(search_geonames(normalized_name))
            candidates.extend(search_wikidata(normalized_name))
        elif tag_type == 'persName':
            candidates.extend(search_wikihum(normalized_name))
            candidates.extend(search_wikidata(normalized_name))

        selected_url = ask_gemini_to_disambiguate(name, normalized_name, context, candidates)
        
        if selected_url and selected_url.startswith("http"):
            tag['ref'] = selected_url
            tag['key'] = normalized_name
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

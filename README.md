# text2ner

Aplikacja TEXT2NER przeznaczona jest do wstępnej konwersji dokumentów historycznych do formatu TEI-XML. Dokument w postaci zwykłego tekstu jest przekształcany na struktury xml (nagłówek head, oraz body z elementami div, p). Następnie w tekście wyszukiwane są występujące w nim nazwy własne: osoby oraz miejsca (miejscowości, kraje itp.). Aplikacja zoptymalizowana jest szczególnie do przetwarzania dokumentów historycznych w języku łacińskim, polskim, niemieckim z XIV-XVI wieku.

## Co robi aplikacja

Aplikacja wykonuje dwa główne zadania:

1. rozpoznaje w tekście encje typu `persName` i `placeName`,
2. próbuje zlinkować każdą z nich do konkretnego rekordu w bazie wiedzy.

W praktyce oznacza to, że z nieopracowanego tekstu źródłowego powstaje:

- TEI-XML z tagami `persName` i `placeName`,
- lista encji z rozstrzygniętym odnośnikiem `ref`,
- lista encji, których nie udało się wiarygodnie powiązać z rekordem referencyjnym,
- log diagnostyczny całego przebiegu przetwarzania.

## Ogólny model działania

Przetwarzanie jest hybrydowe:

- model Gemini odpowiada za rozpoznanie encji w surowym tekście, utworzenie znormalizowanych form nazwy osoby miejsca (`key`)
- ten sam model pomaga w ostrożnej analizie formy encji i w końcowym wyborze najlepszego kandydata z baz referencyjnych,
- wyszukiwanie kandydatów odbywa się przez zewnętrzne źródła referencyjne:
  `WikiHum`, `va.wiki.kul.pl` i `Wikidata`,
- w trudniejszych przypadkach dla osób używany jest fallback oparty o polską Wikipedię.

Aplikacja stara się nie “zgadywać na siłę”. Jeżeli kandydat nie pasuje wystarczająco dobrze, encja pozostaje bez `ref`, ale zachowuje znormalizowany `key`.

## Procedura przetwarzania

Poniżej znajduje się uproszczony opis przebiegu pracy aplikacji od wejścia do wyniku.

### 1. Przyjęcie tekstu

Użytkownik wkleja tekst do interfejsu WWW, a aplikacja wysyła go do endpointu `POST /process`.

Na tym etapie:

- tworzony jest plik logu diagnostycznego w katalogu `log/`,
- tekst wejściowy jest przycinany do 5000 znaków,
- z całego tekstu wyciągane są daty, które później pomagają oceniać zgodność chronologiczną kandydatów z baz referencyjnych.

### 2. Rozpoznanie encji w tekście

Model Gemini otrzymuje instrukcje:

- otagowania osób jako `persName`,
- otagowania miejsc jako `placeName`,
- zwrócenia wyniku w strukturze zgodnej z TEI.

### 3. Iteracja po każdej encji

Dla każdego znacznika `persName` i `placeName` w TEI-XML aplikacja:

- pobiera tekst encji z dokumentu,
- ustala najbliższy kontekst tekstowy,
- sprawdza lokalny cache wyników, aby nie rozwiązywać wielokrotnie tej samej encji w obrębie jednego dokumentu.

Jeżeli wcześniej udało się już skutecznie rozstrzygnąć identyczną encję, wynik jest używany ponownie.

### 4. Analiza formy encji

Jeżeli encja nie została jeszcze rozstrzygnięta, uruchamiany jest pipeline `link_entity(...)`.

Najpierw przygotowywana jest pomocnicza analiza, będąca bazą do wyszukiwania. Obejmuje ona między innymi:

- ostrożnie znormalizowaną formę `normalized_best`,
- warianty lematyczne i powierzchniowe,
- rozpoznane urzędy lub funkcje,
- wskazówki lokalizacyjne związane z encją,
- krótkie wskazówki kontekstowe,
- ewentualne daty występujące w kontekście encji i w całym dokumencie.

### 5. Budowa planu zapytań

Na podstawie analizy formy budowany jest zestaw zapytań do źródeł referencyjnych.

Plan może obejmować:

- formę dokładnie taką, jak w tekście,
- formę znormalizowaną,
- polskie odpowiedniki często spotykanych nazw,
- połączenia typu imię plus urząd,
- połączenia typu imię plus miejsce,
- bardziej specyficzne warianty, jeśli wynikają z kontekstu.

### 6. Zbieranie kandydatów

W pierwszej kolejności aplikacja szuka kandydatów w wyspecjalizowanych historycznych bazach referencyjnych:

- w `WikiHum`,
- w `va.wiki.kul.pl`.

Wyszukiwanie odbywa się przez `Special:Search` z prostym rozmyciem (`~2`). Następnie, jeżeli wyszukiwanie zwróciło listę kandydatów do identyfikacji:

- przez API Wikibase pobierane są ich dane,
- lista filtrowana jest po typie encji na podstawie `instance of`,
- lista porządkowana jest według jakości dopasowania nazwy, zgodności chronologicznej i specyficzności zapytania.

Jeżeli bazy historyczne nie zwrócą wyników, aplikacja przechodzi do wyszukiwania w `Wikidata`.

### 7. Ocena chronologiczna kandydatów

Dla `persName` aplikacja próbuje porównać lata z kontekstu dokumentu z datami życia kandydata pobranymi z właściwości encji.

Na tej podstawie kandydat może zostać oceniony jako:

- zgodny chronologicznie,
- niejednoznaczny chronologicznie,
- sprzeczny z kontekstem.

Ten etap pozwala uporządkować kandydatów i dostarczyć Gemini lepszego materiału do rozstrzygnięcia.

### 8. Wybór najlepszego kandydata

Gdy lista kandydatów jest gotowa przekazywana jest do Gemini wraz z:

- kontekstem encji,
- wariantami nazwy,
- urzędami i wskazówkami miejscowymi,
- oceną chronologiczną,
- opisami, aliasami i wybranymi faktami z właściwości encji.

Model ma wybrać:

- pełny URL najlepszego kandydata,
- albo `NONE`, jeśli nie ma wystarczająco dobrego dopasowania.

Jeżeli wybór się powiedzie, encja dostaje:

- `key` z nazwą znormalizowaną,
- `ref` z URL-em do wybranej encji.

Jeżeli nie, pozostaje samo `key`.

### 9. Dodatkowe próby rozstrzygnięcia

Jeżeli model nie mógł zidentyfikować tagu na podstawie listy kandydatów, aplikacja wykonuje jeszcze jedną próbę identyfikacji na podstawie przeszukiwania Wikipedii (polskiej wersji językowej).

- budowane są bardziej “encyklopedyczne” polskie zapytania,
- aplikacja pobiera tytuł i lead artykułu z Wikipedii,
- przekazuje wyniki modelowi Gemini do analizy.
- jeżeli 

### 10. Końcowy wynik

Po przejściu przez wszystkie encje aplikacja:

- wstawia atrybuty `key` i ewentualnie `ref` do odpowiednich tagów,
- składa końcowy dokument TEI-XML,
- przygotowuje listę encji rozstrzygniętych,
- przygotowuje listę encji nierozstrzygniętych,
- wyświetla wyniki na stronie, pozwalając na pobranie/skopiowanie pliku TEI-XML

### Wymagania

Projekt korzysta z bibliotek wymienionych w `requirements.txt`, w tym:

- `flask`
- `beautifulsoup4`
- `google-genai`
- `python-dotenv`
- `requests`
- `lxml`

## Ograniczenia i uwagi

- Wejściowy tekst jest obecnie ograniczany do 5000 znaków.
- Aplikacja działa najlepiej na tekstach historycznych z wyraźnymi nazwami osób i miejsc.
- Rozstrzyganie encji ma charakter wspomagający, nie gwarantuje pełnej poprawności naukowej i powinno być traktowane jako etap roboczy redakcji cyfrowej.
- W kodzie istnieje semantyczny fallback SPARQL do Wikidaty, ale jest obecnie wyłączony ze względu na problemy z wydajnością zapytań.

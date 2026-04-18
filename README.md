# text2ner

Aplikacja TEXT2NER przeznaczona jest do wstępnej konwersji dokumentów historycznych do formatu TEI-XML. Dokument w postaci zwykłego tekstu jest przekształcany na struktury xml (nagłówek head, oraz body z elementami div, p). Następnie w tekście wyszukiwane są występujące w nim nazwy własne: osoby oraz miejsca (miejscowości, kraje itp.), a także daty, określenia funkcji oraz nazwy instytucji. Aplikacja zoptymalizowana jest szczególnie do przetwarzania dokumentów historycznych w języku łacińskim, polskim, niemieckim z XIV-XVI wieku, związanych z Królestwem Polskim.

## Co robi aplikacja

Aplikacja wykonuje dwa główne zadania:

1. rozpoznaje w tekście encje typu `persName` i `placeName`,
2. próbuje zlinkować każdą z nich do konkretnego rekordu w bazie wiedzy.

W interfejsie WWW oba etapy są rozdzielone:

1. `Rozpoznaj encje` tworzy TEI-XML z tagami, ale bez identyfikacji referencyjnej,
2. `Identyfikuj encje` wykonuje dopiero drugi krok dla `persName` i `placeName`.

Dodatkowo aplikacja taguje występujące w tekście daty jako `date`, a jeśli jest to możliwe, uzupełnia atrybut `when` z datą w postaci ISO.
Może też oznaczać urzędy, godności i funkcje jako `roleName` oraz instytucje jako `orgName`, bez prób identyfikacji tych elementów w bazach referencyjnych.

W praktyce oznacza to, że z nieopracowanego tekstu źródłowego powstaje:

- TEI-XML z tagami `persName`, `placeName`, `orgName`, `date` i `roleName`,
- lista encji z rozstrzygniętym odnośnikiem `ref`,
- lista encji, których nie udało się wiarygodnie powiązać z rekordem referencyjnym,
- log diagnostyczny bieżącego przebiegu rozpoznawania albo identyfikacji.

Interfejs WWW udostępnia obecnie także dwa dodatkowe obszary konfiguracji:

- okno `Parametry`, w którym można wybrać model Gemini i zakres rozpoznawanych tagów,
- okno `Słowniki`, w którym można edytować wybrane słowniki pomocnicze bez ingerencji w kod programu.

## Ogólny model działania

Przetwarzanie wykorzystuje modele językowe i referencyjne bazy wiedzy:

- model Gemini odpowiada za rozpoznanie encji w surowym tekście oraz przygotowanie znormalizowanych form nazw osób i miejsc używanych później jako `key`,
- ten sam model pomaga w ostrożnej analizie formy encji i w końcowym wyborze najlepszego kandydata z baz referencyjnych,
- wyszukiwanie kandydatów odbywa się przez zewnętrzne źródła referencyjne:
  `WikiHum`, `va.wiki.kul.pl` i `Wikidata`,
- w trudniejszych przypadkach dla osób używany jest fallback oparty o polską Wikipedię.

Jeżeli żaden z kandydatów znalezionych w bazach nie pasuje wystarczająco dobrze, encja pozostaje bez `ref`, ale zachowuje znormalizowany `key`.

## Procedura przetwarzania

Poniżej znajduje się uproszczony opis przebiegu pracy aplikacji od wejścia do wyniku.

Pełny diagram procesu jest dostępny w trzech wersjach:

- [PROCESS_DIAGRAM.dot](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM.dot) - źródło Graphviz
- [PROCESS_DIAGRAM.svg](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM.svg) - wersja do szybkiego podglądu
- [PROCESS_DIAGRAM.pdf](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM.pdf) - wersja do druku i udostępniania

Dostępna jest także uproszczona wersja diagramu, przygotowana z myślą o użytkownikach nietechnicznych:

- [PROCESS_DIAGRAM_SIMPLE.dot](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM_SIMPLE.dot) - źródło uproszczonego diagramu
- [PROCESS_DIAGRAM_SIMPLE.svg](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM_SIMPLE.svg) - uproszczony diagram do szybkiego podglądu
- [PROCESS_DIAGRAM_SIMPLE.pdf](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM_SIMPLE.pdf) - uproszczony diagram do druku i prezentacji

### 1. Ustawienie parametrów analizy

Przed uruchomieniem rozpoznawania użytkownik może w oknie `Parametry`:

- wybrać wariant modelu Gemini używany w bieżącym przebiegu,
- włączyć lub wyłączyć typy tagów rozpoznawania: domyślny zestaw to `persName`, `placeName`, `date` i `roleName`, można rozszerzyć go o `orgName`.

Ustawienia są używane przy kolejnym uruchomieniu rozpoznawania, a wybrany model jest również przekazywany do etapu identyfikacji.

### 1a. Edycja słowników pomocniczych

Z poziomu przycisku `Słowniki` użytkownik może otworzyć osobne okno konfiguracyjne z czterema zakładkami odpowiadającymi czterem słownikom pomocniczym:

- osób,
- miejsc,
- przymiotników miejscowych,
- urzędów i funkcji.

Słowniki te zawierają polskie odpowiedniki łacińskich, niemieckich nazw, które są wykorzystywane do tworzenia wariantów wyszukiwania w instancjach wikibase i polskiej wikipedii.

W każdej zakładce można:

- przeglądać aktualne pary `klucz -> wartość`,
- dodawać nowe wiersze,
- usuwać istniejące wiersze,
- zapisać cały słownik do pliku JSON.

Przy zapisie aplikacja:

- waliduje, czy każdy wiersz ma niepusty klucz i wartość,
- odrzuca zduplikowane klucze,
- tworzy kopię bezpieczeństwa pliku `*.bak`,
- przeładowuje zapisany słownik w pamięci aplikacji bez potrzeby restartu serwera.

Aplikacja posiada też szereg słowników wewnętrznych, obecnie niedostępnych dla użytkownika: 

- ORG_NAME_SIGNAL_STEMS - służy do wykrywania, że jakaś fraza bardziej wygląda na instytucję niż na miejsce, po tagowaniu przez Gemini aplikacja robi jeszcze korektę i może przepisać oczywiste błędne placeName na orgName, jeśli tekst wygląda np. na nazwę organizacji kościelnej lub urzędu, a nie na rzeczywiste miejsce.

- HUMAN_TYPE_MARKERS - "awaryjne" znaczniki typu „human”, „person”, „osoba”, „persona”. Przy filtrowaniu kandydatów z baz referencyjnych (instancji wikkibase) aplikacja weryfikuje czy dany element jest człowiekiem, robi to po QID (aplikacja ma listę elementów występujących w różnych wiki określających we właściwości instance of czy element jest człowiekiem np. Q5 w WikiHum), ale awaryjnie rozpoznaje także po etykiecie elementów w instance of porównując z tą listą

- PLACE_TYPE_MARKERS -  lista znaczników dla miejsc i jednostek terytorialnych, działa jak lista HUMAN_TYPE_MARKERS, pomaga rozpoznać, że kandydat z bazy jest miejscem, regionem, krajem albo jednostką administracyjną

- GENERIC_PLACE_SIGNAL_TOKENS -  aplikacja  podstawie tej listy odrzuca zbyt ogólne określenia (jak civitas, terra, regio, urbs), takie słowa są pomijane jako zbyt mało informacyjne. Dzięki temu aplikacja większą wagę przywiązuje do elementów bardziej konkretnych, na przykład nazw regionów, krajów, miast albo przymiotników typu „krakowski”, „pomorski”, „pruski”, zamiast opierać się na samych ogólnikach typu „miasto” czy „kraina”. 

### 2. Rozpoznanie encji w tekście

Użytkownik uruchamia procedurę przyciskiem `Rozpoznaj encje`.

Na tym etapie:

- tworzony jest nowy plik logu diagnostycznego w katalogu `log/`,
- przed utworzeniem nowego logu usuwane są automatycznie pliki logów starsze niż 48 godzin,
- tekst wejściowy jest przycinany do 5000 znaków,
- Gemini wykonuje pierwsze przejście tagowania XML,
- Gemini wykonuje drugie korekcyjne tagowanie, który próbuje uzupełnić pominięte tagi i poprawić oczywiste pomyłki,
- wynik jest normalizowany do pełnego dokumentu TEI-XML.

Rozpoznawanie może oznaczać:

- osoby jako `persName`,
- miejsca jako `placeName`,
- daty jako `date`, a jeśli to możliwe także z atrybutem `when` w formacie ISO,
- funkcje i urzędy jako `roleName`,
- instytucje jako `orgName`.

Po tym etapie użytkownik otrzymuje TEI-XML bez identyfikacji referencyjnej.

### 3. Podgląd i ręczna korekta tagów

Po rozpoznaniu aplikacja pokazuje:

- kod XML,
- podgląd tekstu z kolorowaniem tagów,
- przycisk pobrania XML,
- przycisk pobrania pełnego logu diagnostycznego.

Przed identyfikacją użytkownik może ręcznie poprawić wynik rozpoznania w widoku podglądu:

- usunąć tag,
- zmienić jego typ,
- dodać nowy tag do zaznaczonego fragmentu tekstu.

Dzięki temu identyfikacja może być uruchamiana już na poprawionym przez użytkownika TEI-XML.

### 4. Identyfikacja encji

Identyfikacja rozpoczyna się, kiedy użytkownik wybierze przycisk `Identyfikuj encje`.

Ten etap dotyczy tylko tagów `persName` i `placeName`. Tagi `date`, `roleName` i `orgName` nie są linkowane do zewnętrznych baz referencyjnych.

Na wejściu etapu identyfikacji aplikacja:

- wprowadza informację do plik logu diagnostycznego,
- wyciąga zawartość sekcji `<body>` z TEI-XML,
- wykrywa daty obecne w całym dokumencie, które mogą być przydatne przy weryfikacji kandydatów z baz referencyjnych dla tagów persName (osób), chronologia obecna w dokumencie może pomóc w odrzuceniu kandydatów z innych epok.

### 5. Iteracja po encjach i cache w obrębie dokumentu

Dla każdego znacznika `persName` i `placeName` aplikacja:

- pobiera formy encji,
- ustala najbliższy kontekst tekstowy,
- sprawdza podręczny cache wyników dla bieżącego dokumentu.

Jeżeli ta sama encja została już wcześniej skutecznie rozpoznana w danym XML-u, wynik jest używany ponownie bez powtarzania pełnej procedury identyfikacyjnej.

### 6. Analiza formy encji

Jeżeli encja nie została jeszcze rozpoznana, uruchamiany jest pipeline `link_entity(...)`.

Najpierw Gemini przygotowuje pomocniczą analizę formy, obejmującą między innymi:

- ostrożnie znormalizowaną formę `normalized_best`,
- warianty lematyczne i powierzchniowe,
- rozpoznane urzędy lub funkcje,
- wskazówki kontekstowe (lokalizacyjne, relacyjne, dotyczące np. funkcji osób)
- lata wykryte w kontekście encji.

Na tej podstawie budowany jest plan zapytań do źródeł referencyjnych.

### 7. Zbieranie kandydatów

W pierwszej kolejności aplikacja szuka kandydatów w wyspecjalizowanych źródłach historycznych:

- `WikiHum`,
- `va.wiki.kul.pl`.

Wyszukiwanie odbywa się przez `Special:Search` z prostym rozmyciem (`~2`). Następnie:

- przez API Wikibase pobierane są dane kandydatów,
- lista filtrowana jest po typie encji (np. dla tagów persName filtrowane są elementy będące ludźmi - instance of = human),
- kandydaci są deduplikowani i porządkowani.

Jeżeli wyspecjalizowane źródła nie zwrócą kandydatów, albo Gemini nie wybierze żadnego z nich, aplikacja wykonuje kolejną próbę z kandydatami pobranymi z `Wikidata`.

### 8. Ocena kandydatów i wybór przez Gemini

Dla `persName` kandydaci są dodatkowo oceniani chronologicznie na podstawie lat z dokumentu i dat życia pobranych z danych referencyjnych. Dla `placeName` chronologia nie jest używana.

Do końcowego rozstrzygnięcia Gemini otrzymuje między innymi:

- kontekst encji,
- warianty nazwy,
- wskazówki urzędowe lub miejscowe,
- opisy, aliasy i wybrane fakty z właściwości encji,
- dla osób także ocenę chronologiczną kandydatów.

Model zwraca:

- `selected_url`, jeśli wybór jest wystarczająco pewny,
- albo `NONE`, jeśli żaden kandydat nie pasuje dostatecznie dobrze,
- krótkie uzasadnienie oraz listę sygnałów, które wpłynęły na decyzję.

Jeżeli wybór się powiedzie, encja dostaje:

- `key` z nazwą znormalizowaną,
- `ref` z URL-em do wybranej encji.

Jeżeli nie, pozostaje samo `key`.

### 9. Dodatkowe próby rozstrzygnięcia dla osób

Dla części encji osobowych aplikacja może uruchomić dodatkowy fallback oparty o polską Wikipedię.

W tym wariancie:

- budowane są bardziej encyklopedyczne polskie zapytania,
- pobierane są tytuły, `pageprops` i leady artykułów,
- wyniki są mapowane na odpowiadające im rekordy Wikidaty,
- Model Gemini dostaje dodatkowy materiał do ponownego rozstrzygnięcia.

### 10. Końcowy wynik

Po przejściu przez wszystkie encje aplikacja:

- wstawia atrybuty `key` i ewentualnie `ref` do odpowiednich tagów,
- składa końcowy dokument TEI-XML,
- przygotowuje listę encji rozstrzygniętych,
- przygotowuje listę encji nierozstrzygniętych,
- udostępnia pełny log diagnostyczny oraz fragmenty logu dla poszczególnych encji,
- wyświetla wynik w interfejsie, umożliwiając pobranie lub skopiowanie XML.

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

## Konfiguracja słowników

Część słowników merytorycznych została wydzielona do katalogu `config/`, aby mogły być uzupełniane bez edycji kodu programu. Dotyczy to obecnie plików:

- `config/person_equivalents.json`
- `config/place_equivalents.json`
- `config/place_adjectival_equivalents.json`
- `config/plwiki_office_equivalents.json`

Pliki te można:

- edytować ręcznie jako zwykłe pliki JSON,
- edytować z poziomu interfejsu WWW w oknie `Słowniki`.

Przy zapisie z poziomu aplikacji tworzona jest kopia bezpieczeństwa `*.bak`, a nowa wersja słownika jest od razu przeładowywana w pamięci procesu.

Znaczenie poszczególnych słowników jest następujące:

- `POLISH_PERSON_EQUIVALENTS` - słownik łacińskich lub historycznych form imion z polskimi odpowiednikami. Pozwala wyszukiwać różne warianty tych samych osób w bazach referencyjnych.
- `POLISH_PLACE_EQUIVALENTS` - słownik historycznych i łacińskich nazw miejsc z polskimi odpowiednikami. Dzięki temu aplikacja może przechodzić od form historycznych do form polskich częściej spotykanych w bazach referencyjnych i Wikipedii.
- `POLISH_PLACE_ADJECTIVAL_EQUIVALENTS` - mapowanie nazw miejsc na polskie przymiotniki, np. `pomerania -> pomorski`, `prussia -> pruski`. Pozwala budować bardziej naturalne polskie frazy do wyszukiwania, np. nie tylko `Chełmno`, ale też `chełmiński`.
- `PLWIKI_OFFICE_EQUIVALENTS` - słownik różnych form urzędów i ról z polskimi odpowiednikami, np. `cardinalis -> kardynał`, `episcopus -> biskup`, `cancellarius -> kanclerz`. Służy głównie dodatkowemu wyszukiwaniu opartemu o polską Wikipedię.

Każdy wpis ma postać prostego mapowania tekstowego, np.:

```json
{
  "cracovia": "Kraków",
  "cardinalis": "kardynał"
}
```

W praktyce oznacza to, że historyk lub redaktor może stopniowo rozbudowywać zaplecze słownikowe aplikacji bez modyfikowania kodu Python.

## Diagram procesu

Diagram procesu rozpoznawania i identyfikacji dostępny jest jako:

- [PROCESS_DIAGRAM.dot](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM.dot) - plik źródłowy
- [PROCESS_DIAGRAM.svg](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM.svg) - podstawowa wersja do przeglądania
- [PROCESS_DIAGRAM.pdf](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM.pdf) - wersja do eksportu i wydruku
- [PROCESS_DIAGRAM_SIMPLE.dot](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM_SIMPLE.dot) - uproszczony plik źródłowy
- [PROCESS_DIAGRAM_SIMPLE.svg](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM_SIMPLE.svg) - uproszczona wersja do przeglądania
- [PROCESS_DIAGRAM_SIMPLE.pdf](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM_SIMPLE.pdf) - uproszczona wersja do eksportu i wydruku

Plik źródłowy diagramu (dot) można przetworzyć do svg i pdf za pomocą poleceń:

```bash
dot -Tsvg PROCESS_DIAGRAM.dot -o PROCESS_DIAGRAM.svg
dot -Tpdf PROCESS_DIAGRAM.dot -o PROCESS_DIAGRAM.pdf
dot -Tsvg PROCESS_DIAGRAM_SIMPLE.dot -o PROCESS_DIAGRAM_SIMPLE.svg
dot -Tpdf PROCESS_DIAGRAM_SIMPLE.dot -o PROCESS_DIAGRAM_SIMPLE.pdf
```

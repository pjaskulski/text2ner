# TEXT2NER: diagram procesu

Diagram procesu przetwarzania w projekcie `text2ner` jest utrzymywany w Graphviz. Wersja Mermaid została usunięta, aby pozostał jeden kanoniczny opis przepływu.

## Dostępne pliki

- [PROCESS_DIAGRAM.dot](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM.dot) - źródło diagramu w formacie Graphviz DOT
- [PROCESS_DIAGRAM.svg](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM.svg) - wersja do szybkiego podglądu w przeglądarce
- [PROCESS_DIAGRAM.pdf](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM.pdf) - wersja do druku, udostępniania i osadzania w dokumentacji
- [PROCESS_DIAGRAM_SIMPLE.dot](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM_SIMPLE.dot) - uproszczona wersja źródłowa, bardziej przystępna dla użytkowników nietechnicznych
- [PROCESS_DIAGRAM_SIMPLE.svg](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM_SIMPLE.svg) - uproszczony diagram do szybkiego podglądu
- [PROCESS_DIAGRAM_SIMPLE.pdf](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM_SIMPLE.pdf) - uproszczony diagram do druku i prezentacji

## Co pokazuje diagram

Diagram obejmuje pełny przebieg przetwarzania:

- standardowy workflow `POST /recognize` -> podgląd / opcjonalna korekta -> `POST /identify`,
- tworzenie logu diagnostycznego i automatyczne czyszczenie logów starszych niż 48 godzin,
- dwuprzebiegowe tagowanie encji przez Gemini z uwzględnieniem wybranych typów tagów,
- iterację po encjach `persName` i `placeName` podczas identyfikacji,
- analizę formy encji, budowę planu zapytań i wybór kandydata przez Gemini,
- wyszukiwanie kandydatów w `WikiHum`, `va.wiki.kul.pl`, `Wikidata` oraz fallback przez polską Wikipedię dla części encji osobowych,
- złożenie końcowego TEI-XML, list encji oraz odpowiedzi JSON zwracanej do interfejsu.

Równolegle utrzymywana jest także wersja uproszczona diagramu, przeznaczona bardziej dla historyków i użytkowników końcowych niż dla programistów. Pokazuje ona główne etapy pracy z aplikacją bez wchodzenia w szczegóły implementacyjne.

## Aktualizacja renderów

Po zmianie pliku źródłowego można odtworzyć artefakty poleceniami:

```bash
dot -Tsvg PROCESS_DIAGRAM.dot -o PROCESS_DIAGRAM.svg
dot -Tpdf PROCESS_DIAGRAM.dot -o PROCESS_DIAGRAM.pdf
dot -Tsvg PROCESS_DIAGRAM_SIMPLE.dot -o PROCESS_DIAGRAM_SIMPLE.svg
dot -Tpdf PROCESS_DIAGRAM_SIMPLE.dot -o PROCESS_DIAGRAM_SIMPLE.pdf
```

## Uwagi

- `SVG` jest najwygodniejszy do pracy w repozytorium i szybkiego podglądu.
- `PDF` lepiej nadaje się do obiegu redakcyjnego, druku i załączania do innych materiałów.
- Plik `DOT` jest jedyną wersją źródłową, z której należy generować pozostałe formaty.

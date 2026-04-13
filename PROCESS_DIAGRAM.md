# TEXT2NER: diagram procesu

Diagram procesu przetwarzania w projekcie `text2ner` jest utrzymywany w Graphviz. Wersja Mermaid została usunięta, aby pozostał jeden kanoniczny opis przepływu.

## Dostępne pliki

- [PROCESS_DIAGRAM.dot](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM.dot) - źródło diagramu w formacie Graphviz DOT
- [PROCESS_DIAGRAM.svg](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM.svg) - wersja do szybkiego podglądu w przeglądarce
- [PROCESS_DIAGRAM.pdf](/home/piotr/ihpan/text2ner/PROCESS_DIAGRAM.pdf) - wersja do druku, udostępniania i osadzania w dokumentacji

## Co pokazuje diagram

Diagram obejmuje pełny przebieg przetwarzania:

- przyjęcie tekstu przez endpoint `POST /process`,
- tagowanie encji przez Gemini,
- iterację po encjach `persName` i `placeName`,
- analizę formy i budowę planu zapytań,
- wyszukiwanie kandydatów w `WikiHum`, `va.wiki.kul.pl` i `Wikidata`,
- dodatkowy fallback przez polską Wikipedię dla części encji osobowych,
- złożenie końcowego TEI-XML oraz odpowiedzi JSON.

## Aktualizacja renderów

Po zmianie pliku źródłowego można odtworzyć artefakty poleceniami:

```bash
dot -Tsvg PROCESS_DIAGRAM.dot -o PROCESS_DIAGRAM.svg
dot -Tpdf PROCESS_DIAGRAM.dot -o PROCESS_DIAGRAM.pdf
```

## Uwagi

- `SVG` jest najwygodniejszy do pracy w repozytorium i szybkiego podglądu.
- `PDF` lepiej nadaje się do obiegu redakcyjnego, druku i załączania do innych materiałów.
- Plik `DOT` jest jedyną wersją źródłową, z której należy generować pozostałe formaty.

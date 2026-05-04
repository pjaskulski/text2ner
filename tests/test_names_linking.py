import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

import names_linking


class AugustinoIdentificationSignalsTest(unittest.TestCase):
    def test_default_wikidata_request_interval_is_three_seconds(self):
        self.assertEqual(names_linking.WIKIDATA_REQUEST_INTERVAL_SECONDS, 3.0)

    def test_wikimedia_user_agent_uses_configured_contact(self):
        with patch.object(names_linking, "WIKIMEDIA_USER_AGENT", ""), \
                patch.object(names_linking, "WIKIMEDIA_USER_AGENT_CONTACT", "ops@example.test"), \
                patch.object(names_linking, "TEXT2NER_USER_AGENT_NAME", "Text2NERBot"), \
                patch.object(names_linking, "TEXT2NER_USER_AGENT_VERSION", "1.1"):
            user_agent = names_linking.build_wikimedia_user_agent("enwiki person fallback")

        self.assertEqual(
            user_agent,
            "Text2NERBot/1.1 (ops@example.test) python-requests enwiki person fallback",
        )

    def test_wikimedia_user_agent_can_be_fully_overridden(self):
        with patch.object(
            names_linking,
            "WIKIMEDIA_USER_AGENT",
            "CustomAgent/2.0 (https://example.test/contact) requests",
        ):
            headers = names_linking.wikimedia_headers(
                "ignored purpose",
                {"Accept": "application/json"},
            )

        self.assertEqual(
            headers["User-Agent"],
            "CustomAgent/2.0 (https://example.test/contact) requests",
        )
        self.assertEqual(headers["Accept"], "application/json")

    def test_analyze_form_logs_raw_response_when_gemini_returns_invalid_json(self):
        class FakeResponse:
            text = '{"normalized_best": "Johannes" "confidence_form": "high"}'

        diagnostic_messages = []
        fake_models = unittest.mock.Mock()
        fake_models.generate_content.return_value = FakeResponse()
        fake_client = unittest.mock.Mock(models=fake_models)

        with patch.object(names_linking, "client", fake_client), \
                patch.object(names_linking, "diagnostic_log", side_effect=diagnostic_messages.append):
            result = names_linking.analyze_form_with_gemini(
                "Johannes",
                "Johannes biskup Miśni",
                "persName",
                document_years=[1501],
            )

        self.assertEqual(result["normalized_best"], "Johannes")
        self.assertTrue(any("raw_response=" in message for message in diagnostic_messages))
        self.assertTrue(any("Fallback analizy encji 'Johannes'" in message for message in diagnostic_messages))
        call_kwargs = fake_models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["config"].response_mime_type, "application/json")

    def test_cleanup_tags_untagged_latin_day_month_year_date(self):
        tagged_xml = """
        <div type="document">
          <p>Perusii, Iovis1 19 decembris 1392</p>
          <p>[m. s.] Episcopi Cracoviensis</p>
        </div>
        """

        cleaned = names_linking.cleanup_tagged_xml_output(
            tagged_xml,
            enabled_tag_types=["date", "persName", "placeName"],
        )

        soup = BeautifulSoup(cleaned, "xml")
        date_tag = soup.find("date")
        self.assertIsNotNone(date_tag)
        self.assertEqual(date_tag.get("when"), "1392-12-19")
        self.assertEqual(date_tag.get_text(strip=True), "19 decembris 1392")

    def test_cleanup_does_not_retag_existing_date(self):
        tagged_xml = """
        <div type="document">
          <p>Perusii, Iovis1 <date when="1392-12-19">19 decembris 1392</date></p>
        </div>
        """

        cleaned = names_linking.cleanup_tagged_xml_output(
            tagged_xml,
            enabled_tag_types=["date", "persName", "placeName"],
        )

        self.assertEqual(cleaned.count("<date"), 1)

    def test_query_plan_expands_augustinus_to_italian_and_perusia_to_perugia(self):
        entity_analysis = {
            "surface": "Augustino",
            "tag_type": "persName",
            "normalized_best": "Augustinus",
            "lemma_candidates": ["Augustinus"],
            "surface_variants": ["Augustino"],
            "office_terms": ["episcopus Perusinus", "thesaurarius domini pape"],
            "place_terms": ["Perusia"],
        }

        queries = names_linking.build_query_plan(entity_analysis)

        self.assertIn("Agostino", queries)
        self.assertIn("Augustinus Perugia", queries)
        self.assertIn("Augustinus papal treasurer", queries)

    def test_query_plan_adds_particleless_person_name_variants(self):
        entity_analysis = {
            "surface": "Petrum de Strelicz",
            "tag_type": "persName",
            "normalized_best": "Petrus de Strelicz",
            "lemma_candidates": ["Petrus de Strelicz"],
            "surface_variants": ["Petrum de Strelicz"],
            "office_terms": [],
            "place_terms": [],
        }

        queries = names_linking.build_query_plan(entity_analysis)

        self.assertIn("Petrus Strelicz", queries)
        self.assertIn("Piotr Strelicz", queries)
        self.assertIn("Strelicz", queries)

    def test_query_plan_prefers_confident_person_lemma_over_inflected_surface(self):
        entity_analysis = {
            "surface": "Świętosława",
            "tag_type": "persName",
            "normalized_best": "Świętosław",
            "confidence_form": "high",
            "lemma_candidates": ["Świętosław"],
            "surface_variants": ["Świętosława", "Świętosław"],
            "office_terms": [],
            "place_terms": ["Strzelce"],
        }

        queries = names_linking.build_query_plan(entity_analysis)

        self.assertEqual(queries[0], "Świętosław")
        self.assertIn("Świętosław Strzelce", queries)
        self.assertNotIn("Świętosława", queries)

    def test_wikidata_candidate_collection_limits_search_queries(self):
        entity_analysis = {
            "surface": "Augustino",
            "tag_type": "persName",
            "normalized_best": "Augustinus",
            "lemma_candidates": ["Augustinus"],
            "surface_variants": ["Augustino"],
            "office_terms": ["episcopus Perusinus", "thesaurarius domini pape"],
            "place_terms": ["Perusia"],
        }
        seen_queries = []

        def fake_search(query, tag_type, source_config):
            seen_queries.append(query)
            return []

        original_limit = names_linking.WIKIDATA_MAX_SEARCH_QUERIES
        names_linking.WIKIDATA_MAX_SEARCH_QUERIES = 3
        try:
            with patch.object(names_linking, "search_source_candidates", side_effect=fake_search):
                names_linking.collect_candidates_from_sources(
                    entity_analysis,
                    "persName",
                    ("Wikidata",),
                )
        finally:
            names_linking.WIKIDATA_MAX_SEARCH_QUERIES = original_limit

        self.assertEqual(len(seen_queries), 3)

    def test_wikidata_candidate_collection_stops_after_rate_limit(self):
        entity_analysis = {
            "surface": "Augustino",
            "tag_type": "persName",
            "normalized_best": "Augustinus",
            "lemma_candidates": ["Augustinus"],
            "surface_variants": ["Augustino"],
            "office_terms": [],
            "place_terms": [],
        }
        seen_queries = []

        def fake_search(query, tag_type, source_config):
            seen_queries.append(query)
            raise names_linking.WikidataRateLimitError("HTTP 429")

        with patch.object(names_linking, "search_source_candidates", side_effect=fake_search):
            names_linking.collect_candidates_from_sources(
                entity_analysis,
                "persName",
                ("Wikidata",),
            )

        self.assertEqual(len(seen_queries), 1)

    def test_enwiki_fallback_queries_use_english_office_and_place_terms(self):
        entity_analysis = {
            "surface": "Augustino",
            "tag_type": "persName",
            "normalized_best": "Augustinus",
            "lemma_candidates": ["Augustinus"],
            "surface_variants": ["Augustino"],
            "office_terms": ["episcopus Perusinus", "thesaurarius domini pape"],
            "place_terms": ["Perusia"],
        }

        queries = names_linking.build_wikipedia_person_fallback_queries(
            entity_analysis,
            "enwiki",
        )

        self.assertIn("Augustinus bishop Perugia", queries)
        self.assertIn("Augustinus treasurer", queries)
        self.assertIn("Augustinus papal", queries)

    def test_wikipedia_fallback_collects_plwiki_and_enwiki_candidates(self):
        entity_analysis = {
            "surface": "Augustino",
            "tag_type": "persName",
            "normalized_best": "Augustinus",
            "office_terms": ["episcopus Perusinus"],
            "place_terms": ["Perusia"],
            "context_years": [1392],
            "posthumous_context": False,
        }
        seen_sources = []

        def fake_collect(entity_analysis, source_key):
            seen_sources.append(source_key)
            return [{
                "source": "Wikidata",
                "id": "Q1",
                "url": "https://www.wikidata.org/entity/Q1",
                "name": "Augustinus test",
                "labels": {},
                "descriptions": {},
                "aliases": {},
                "instance_of_texts": ["human"],
                "priority_claim_facts": [],
                "claim_facts": [],
                "matched_queries": [source_key],
            }]

        with patch.object(
            names_linking,
            "collect_wikipedia_person_fallback_candidates",
            side_effect=fake_collect,
        ):
            candidates = names_linking.collect_wikipedia_person_fallback_candidates_from_all_sources(
                entity_analysis
            )

        self.assertEqual(seen_sources, ["plwiki", "enwiki"])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["id"], "Q1")

    def test_collect_candidates_uses_wikidata_after_specialized_sources_without_hits(self):
        entity_analysis = {
            "surface": "Augustino",
            "tag_type": "persName",
            "normalized_best": "Augustinus",
            "office_terms": ["episcopus Perusinus"],
            "place_terms": ["Perusia"],
            "context_years": [1392],
            "posthumous_context": False,
        }
        calls = []
        wikidata_candidate = {
            "source": "Wikidata",
            "id": "Q2",
            "url": "https://www.wikidata.org/entity/Q2",
            "name": "Augustinus wikidata",
            "labels": {},
            "descriptions": {},
            "aliases": {},
            "instance_of_texts": ["human"],
            "priority_claim_facts": [],
            "claim_facts": [],
            "matched_queries": ["Augustinus"],
        }

        def fake_collect_from_sources(entity_analysis, tag_type, source_names):
            calls.append(tuple(source_names))
            if tuple(source_names) == ("Wikidata",):
                return [wikidata_candidate]
            return []

        with patch.object(names_linking, "build_query_plan", return_value=["Augustinus"]), \
                patch.object(names_linking, "collect_candidates_from_sources", side_effect=fake_collect_from_sources):
            candidates = names_linking.collect_candidates(entity_analysis, "", "persName")

        self.assertEqual(calls, [("WikiHum", "va.wiki.kul.pl"), ("Wikidata",)])
        self.assertEqual(candidates, [wikidata_candidate])

    def test_link_entity_tries_wikidata_before_wikipedia_when_specialized_candidates_are_rejected(self):
        entity_analysis = {
            "surface": "Johannes",
            "tag_type": "persName",
            "normalized_best": "Johannes",
            "lemma_candidates": ["Johannes"],
            "surface_variants": ["Johannes"],
            "office_terms": ["Bischoff zcu Meyssenn"],
            "place_terms": ["Meyssen"],
            "context_years": [1501],
            "posthumous_context": False,
        }
        local_candidate = {
            "source": "WikiHum",
            "id": "Q1",
            "url": "https://wikihum.lab.dariah.pl/entity/Q1",
            "name": "Wrong Johannes",
            "matched_queries": ["Johannes"],
        }
        wikidata_candidate = {
            "source": "Wikidata",
            "id": "Q2",
            "url": "https://www.wikidata.org/entity/Q2",
            "name": "Johann von Saalhausen",
            "matched_queries": ["Wikidata"],
        }
        decision_calls = []

        def fake_decision(name, context, tag_type, analysis, candidates):
            decision_calls.append([candidate["id"] for candidate in candidates])
            if candidates == [wikidata_candidate]:
                return {
                    "status": "selected",
                    "selected_url": wikidata_candidate["url"],
                    "selected_candidate": wikidata_candidate,
                }
            return {"status": "none", "selected_url": None, "reason": "gemini_none"}

        with patch.object(names_linking, "analyze_name_with_gemini", return_value=entity_analysis), \
                patch.object(names_linking, "build_query_plan", return_value=["Johannes"]), \
                patch.object(names_linking, "collect_candidates", return_value=[local_candidate]), \
                patch.object(names_linking, "build_link_decision", side_effect=fake_decision), \
                patch.object(
                    names_linking,
                    "collect_wikipedia_person_fallback_candidates_from_all_sources",
                    return_value=[],
                ) as collect_wikipedia, \
                patch.object(
                    names_linking,
                    "collect_wikidata_only_candidates",
                    return_value=[wikidata_candidate],
                ) as collect_wikidata:
            result = names_linking.link_entity("Johannes", "Johannes biskup Miśni", "persName")

        collect_wikidata.assert_called_once()
        collect_wikipedia.assert_not_called()
        self.assertEqual(decision_calls, [["Q1"], ["Q2"]])
        self.assertEqual(result["decision"]["selected_url"], wikidata_candidate["url"])

    def test_candidate_with_papal_treasurer_signal_ranks_above_generic_bishop(self):
        entity_analysis = {
            "surface": "Augustino",
            "tag_type": "persName",
            "normalized_best": "Augustinus",
            "office_terms": ["episcopus Perusinus", "thesaurarius domini pape"],
            "place_terms": ["Perusia"],
            "context_years": [1392],
            "posthumous_context": False,
        }
        target_candidate = {
            "source": "va.wiki.kul.pl",
            "id": "Q4980",
            "url": "https://va.wiki.kul.pl/entity/Q4980",
            "name": "Augustinus de Lanzano",
            "labels": {"la": "Augustinus de Lanzano"},
            "descriptions": {},
            "aliases": {},
            "instance_of_texts": ["persona"],
            "priority_claim_facts": [
                "opisany jako: Penne-Atri, episcopus",
                "opisany jako: Sedes Apostolica, thesaurarius",
            ],
            "claim_facts": [],
            "matched_queries": ["Augustinus"],
        }
        generic_candidate = {
            "source": "va.wiki.kul.pl",
            "id": "Q1980",
            "url": "https://va.wiki.kul.pl/entity/Q1980",
            "name": "Augustinus Conradi de Dzierżoniów",
            "labels": {"la": "Augustinus Conradi de Dzierżoniów"},
            "descriptions": {},
            "aliases": {},
            "instance_of_texts": ["persona"],
            "priority_claim_facts": ["opisany jako: capellanus honoris papae"],
            "claim_facts": [],
            "matched_queries": ["Augustinus"],
        }

        ordered = names_linking.order_candidates_for_review(
            [generic_candidate, target_candidate],
            entity_analysis,
        )

        self.assertEqual(ordered[0]["id"], "Q4980")
        self.assertGreater(
            names_linking.candidate_context_signal_score(target_candidate, entity_analysis),
            names_linking.candidate_context_signal_score(generic_candidate, entity_analysis),
        )

    def test_manual_suggestions_reject_modern_false_friend_profile(self):
        entity_analysis = {
            "surface": "Tomasz",
            "tag_type": "persName",
            "normalized_best": "Tomasz",
            "office_terms": ["officialis ecclesiasticus", "pape thesaurarius"],
            "place_terms": ["Perugia"],
            "context_clues": ["urzędnik kościelny w końcu XIV wieku"],
            "context_years": [1392],
            "posthumous_context": False,
        }
        candidate = {
            "source": "Wikidata",
            "id": "Q124117442",
            "url": "https://www.wikidata.org/entity/Q124117442",
            "name": "Melissa Barrera Tomas",
            "labels": {"en": "Melissa Barrera Tomas"},
            "descriptions": {"en": "Peruvian chemist"},
            "aliases": {},
            "instance_of_texts": ["human"],
            "priority_claim_facts": [],
            "claim_facts": [
                "occupation: chemist",
                "sex or gender: female",
                "country of citizenship: Peru",
                "given name: Melissa",
            ],
            "matched_queries": ["Tomasz Perugia"],
        }

        self.assertFalse(
            names_linking.candidate_is_plausible_manual_suggestion(candidate, entity_analysis)
        )
        self.assertEqual(
            names_linking.candidate_manual_rejection_reason(candidate, entity_analysis),
            "incompatible_gender_for_ecclesiastical_office",
        )

    def test_manual_suggestions_keep_ecclesiastical_candidate_without_life_dates(self):
        entity_analysis = {
            "surface": "Augustino",
            "tag_type": "persName",
            "normalized_best": "Augustinus",
            "office_terms": ["episcopus Perusinus", "thesaurarius domini pape"],
            "place_terms": ["Perusia"],
            "context_clues": ["biskup i skarbnik papieski"],
            "context_years": [1392],
            "posthumous_context": False,
        }
        candidate = {
            "source": "va.wiki.kul.pl",
            "id": "Q4980",
            "url": "https://va.wiki.kul.pl/entity/Q4980",
            "name": "Augustinus de Lanzano",
            "labels": {"la": "Augustinus de Lanzano"},
            "descriptions": {},
            "aliases": {},
            "instance_of_texts": ["persona"],
            "priority_claim_facts": [
                "opisany jako: Penne-Atri, episcopus",
                "opisany jako: Sedes Apostolica, thesaurarius",
            ],
            "claim_facts": [],
            "matched_queries": ["Augustinus"],
        }

        self.assertTrue(
            names_linking.candidate_is_plausible_manual_suggestion(candidate, entity_analysis)
        )

    def test_manual_suggestions_reject_modern_public_figure_with_name_only_match(self):
        entity_analysis = {
            "surface": "Thoma",
            "tag_type": "persName",
            "normalized_best": "Thomas",
            "office_terms": ["pape thesaurarius", "officialis ecclesiasticus"],
            "place_terms": [],
            "context_clues": ["urzędnik kościelny w dokumencie łacińskim z końca XIV wieku"],
            "context_years": [1392],
            "posthumous_context": False,
        }
        candidate = {
            "source": "Wikidata",
            "id": "Q137192964",
            "url": "https://www.wikidata.org/entity/Q137192964",
            "name": "Thomas Rose",
            "labels": {"en": "Thomas Rose"},
            "descriptions": {"pl": "ambasador Stanów Zjednoczonych w Polsce (2025–)"},
            "aliases": {},
            "instance_of_texts": ["human"],
            "priority_claim_facts": [],
            "claim_facts": [
                "occupation: journalist",
                "sex or gender: male",
            ],
            "matched_queries": ["Thomas"],
        }

        self.assertFalse(
            names_linking.candidate_is_plausible_manual_suggestion(candidate, entity_analysis)
        )
        self.assertEqual(
            names_linking.candidate_manual_rejection_reason(candidate, entity_analysis),
            "modern_profile_for_historical_ecclesiastical_context",
        )


if __name__ == "__main__":
    unittest.main()

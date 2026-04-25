import unittest
import tempfile
from unittest.mock import patch

from bs4 import BeautifulSoup

import app


def make_link_result(surface, url):
    return {
        "entity_analysis": {
            "context_clues": [],
            "context_years": [],
            "posthumous_context": False,
        },
        "normalized_name": surface,
        "decision": {
            "status": "selected",
            "selected_url": url,
            "selected_candidate": {
                "name": surface,
                "url": url,
                "source": "test",
                "id": url.rsplit("/", 1)[-1],
                "description": "",
                "key_facts": [],
            },
        },
        "candidate_suggestions": [],
    }


class IdentifyEntitiesCacheTest(unittest.TestCase):
    def test_same_surface_in_different_contexts_is_not_reused_from_cache(self):
        soup = BeautifulSoup(
            """
            <div type="document">
              <p><persName>Jan</persName> biskup krakowski.</p>
              <p><persName>Jan</persName> kanclerz koronny.</p>
            </div>
            """,
            "xml",
        )
        calls = []

        def fake_link_entity(name, context, tag_type, document_years=None):
            calls.append((name, context, tag_type, document_years))
            return make_link_result(name, f"https://example.test/{len(calls)}")

        with patch.object(app, "link_entity", side_effect=fake_link_entity):
            entities, unresolved = app.identify_entities_in_soup(soup, document_years=[1501])

        refs = [tag["ref"] for tag in soup.find_all("persName")]
        self.assertEqual(refs, ["https://example.test/1", "https://example.test/2"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(entities), 2)
        self.assertEqual(unresolved, [])


class PreviewPdfHtmlTest(unittest.TestCase):
    def test_build_preview_pdf_html_keeps_entity_color_classes(self):
        xml = """
        <TEI>
          <text>
            <body>
              <p><persName>Jan</persName> był w <placeName>Krakowie</placeName>.</p>
            </body>
          </text>
        </TEI>
        """

        html = app.build_preview_pdf_html(xml)

        self.assertIn('class="tei-tag entity-pers"', html)
        self.assertIn('class="tei-tag entity-place"', html)
        self.assertIn('class="entity-label entity-pers"', html)
        self.assertIn("Legenda oznaczeń", html)
        self.assertIn("<p>", html)

    def test_build_preview_pdf_html_adds_identification_sections(self):
        xml = """
        <TEI>
          <text>
            <body>
              <p><persName ref="https://example.test/Q1">Jan</persName> spotkał <persName>Piotra</persName>.</p>
            </body>
          </text>
        </TEI>
        """

        html = app.build_preview_pdf_html(
            xml,
            entities=[{
                "name": "Jan",
                "surface": "Jan",
                "type": "persName",
                "url": "https://example.test/Q1",
            }],
            unresolved_entities=[{
                "name": "Piotr",
                "surface": "Piotra",
                "type": "persName",
                "reason": "no_candidates",
            }],
            identification_performed=True,
        )

        self.assertIn("Zidentyfikowane encje", html)
        self.assertIn("Niezidentyfikowane encje", html)
        self.assertIn('class="entity-url"', html)
        self.assertIn("https://example.test/Q1", html)
        self.assertIn("no_candidates", html)


class ProgressSessionStorageTest(unittest.TestCase):
    def test_progress_is_available_after_memory_cache_is_cleared(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(app, "PROGRESS_SESSION_DIR", temp_dir):
                app.PROGRESS_SESSIONS.clear()
                app.update_progress(
                    "progress-test-1",
                    status="running",
                    current=2,
                    total=5,
                    message="Identyfikuję 2/5: Jan (persName)",
                )
                app.PROGRESS_SESSIONS.clear()

                progress = app.get_progress("progress-test-1")

        self.assertEqual(progress["status"], "running")
        self.assertEqual(progress["current"], 2)
        self.assertEqual(progress["total"], 5)
        self.assertEqual(progress["message"], "Identyfikuję 2/5: Jan (persName)")


if __name__ == "__main__":
    unittest.main()

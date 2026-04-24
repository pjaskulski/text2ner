import unittest
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


if __name__ == "__main__":
    unittest.main()

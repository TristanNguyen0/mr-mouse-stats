from mr_mouse_stats.liquipedia import api


class StubClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, **params):
        self.calls.append(params)
        return self.responses[len(self.calls) - 1]


def test_fetch_pages_follows_normalization_and_redirects():
    client = StubClient([
        {
            "query": {
                "normalized": [{"from": "energy", "to": "Energy"}],
                "redirects": [{"from": "Energy", "to": "Energy (American player)"}],
                "pages": [
                    {
                        "title": "Energy (American player)",
                        "revisions": [{"slots": {"main": {"content": "text-e"}}}],
                    },
                    {"title": "Nobody", "missing": True},
                ],
            }
        }
    ])
    pages = api.fetch_pages(client, ["energy", "Nobody"])
    assert pages["energy"].title == "Energy (American player)"
    assert pages["energy"].wikitext == "text-e"
    assert not pages["energy"].missing
    assert pages["Nobody"].missing
    assert pages["Nobody"].wikitext is None


def test_fetch_pages_chunks_and_dedupes():
    def response(*titles):
        return {
            "query": {
                "pages": [
                    {
                        "title": t,
                        "revisions": [{"slots": {"main": {"content": f"text-{t}"}}}],
                    }
                    for t in titles
                ]
            }
        }

    client = StubClient([response("A", "B"), response("C")])
    pages = api.fetch_pages(client, ["A", "B", "A", "C"], chunk_size=2)
    assert len(client.calls) == 2
    assert client.calls[0]["titles"] == "A|B"
    assert client.calls[1]["titles"] == "C"
    assert pages["C"].wikitext == "text-C"


def test_fetch_page_single():
    client = StubClient([
        {
            "query": {
                "pages": [
                    {
                        "title": "Solo",
                        "revisions": [{"slots": {"main": {"content": "hi"}}}],
                    }
                ]
            }
        }
    ])
    page = api.fetch_page(client, "Solo")
    assert page.wikitext == "hi"
    assert client.calls[0]["redirects"] == "1"

from crawler.text import clean_text, html_to_text


def test_leading_mention_with_spaces_is_removed():
    assert clean_text("@Birne Helene: Ist das so?") == "Ist das so?"


def test_inline_mentions_removed_but_emails_kept():
    assert clean_text("Danke @max_mustermann für den Hinweis, Mail an a@b.de") == (
        "Danke für den Hinweis, Mail an a@b.de"
    )


def test_whitespace_normalised():
    assert clean_text("  Eins  zwei\r\n\r\n\r\n\r\nDrei  ") == "Eins zwei\n\nDrei"


def test_html_to_text_keeps_breaks():
    assert clean_text(html_to_text("<p>Hallo<br/>Welt</p><p>Absatz</p>")) == "Hallo\nWelt\n\nAbsatz"

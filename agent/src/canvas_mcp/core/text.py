"""Plain-text conversion for Canvas HTML content."""

import html
import re


def strip_html_tags(html_content: str) -> str:
    """Convert HTML to readable plain text.

    Block-level elements (headings, paragraphs, list items, table rows, ``<br>``,
    etc.) become line breaks so adjacent blocks don't run together — e.g.
    ``<h3>Grading</h3><p>Final exam...</p>`` yields ``Grading\nFinal exam...``
    rather than ``GradingFinal exam...``. Inline tags become a space. HTML
    entities are decoded and excess whitespace collapsed (intra-line runs to a
    single space; blank-line runs to at most one).
    """
    if not html_content:
        return ""

    text = html_content

    # Drop <script>/<style> blocks entirely so their JS/CSS contents don't
    # leak into the plain-text output.
    text = re.sub(r'(?is)<(script|style)\b[^>]*>.*?</\1>', '', text)

    # Normalize <br> and block-level boundaries to newlines so content across
    # tag boundaries is separated instead of concatenated.
    text = re.sub(r'(?i)<\s*br\s*/?\s*>', '\n', text)
    text = re.sub(
        r'(?i)</\s*(?:p|div|h[1-6]|li|ul|ol|tr|table|thead|tbody|tfoot|'
        r'section|article|header|footer|blockquote|pre)\s*>',
        '\n',
        text,
    )
    # Separate table cells within a row.
    text = re.sub(r'(?i)</\s*(?:td|th)\s*>', '\t', text)

    # Remove all remaining tags. Use a space so inline tags don't join words.
    text = re.sub(r'<[^>]+>', ' ', text)

    # Decode HTML entities (named, decimal, and hex) via the stdlib — covers
    # smart quotes, dashes, accents, &nbsp;, etc. that Canvas content commonly
    # uses, with no manual entity table to maintain.
    text = html.unescape(text)

    # Collapse intra-line whitespace but preserve line breaks. \xa0 (decoded
    # from &nbsp;) is normalized to a regular space.
    text = re.sub(r'[ \t\xa0]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()

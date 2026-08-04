from __future__ import annotations

from lxml import etree


def parse_xml(raw: bytes):
    """Parse theme XML without loading external DTDs or expanding entities."""
    parser = etree.XMLParser(
        load_dtd=False,
        no_network=True,
        resolve_entities=False,
        huge_tree=False,
    )
    return etree.fromstring(raw, parser=parser)

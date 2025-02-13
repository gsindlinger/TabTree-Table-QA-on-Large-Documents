from __future__ import annotations
from pandas import DataFrame
from pydantic import BaseModel

from .tabtree_model import NodeColor

from ...retrieval.document_preprocessors.table_parser.custom_html_parser import (
    HTMLTableParser,
)
from .tabtree_service import FullTabTree, TabTreeService
from ...retrieval.document_preprocessors.table_serializer import (
    ExtendedTable,
    ExtendedTable,
    TableSerializer,
)


class TabTreeSerializer(TableSerializer):
    table_splitter_backup: str = r"\n"

    def serialize_table_to_extended_table(self, table_str: str) -> ExtendedTable | None:
        html_table = HTMLTableParser.parse_and_clean_table(table_str)
        if not html_table:
            return None
        full_tab_tree = TabTreeService.generate_full_tabtree(html_table)
        serialized_table = TabTreeService.generate_serialized_string(
            full_tab_tree, primary_colour=NodeColor.YELLOW
        )
        return html_table.to_extended_table(serialized_table=serialized_table)

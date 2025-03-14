import logging
from typing import List, Literal, Optional, Tuple
from bs4 import BeautifulSoup, NavigableString, Tag
from pandas import DataFrame
from pydantic import BaseModel

from .custom_cell import CustomCell
from .custom_table import CustomTable, CustomTableWithHeaderOptional


class HTMLTableParser(BaseModel):
    bs4_table: Optional[Tag] = None
    table: CustomTable = []
    num_columns: int = 0
    num_rows: int = 0
    thead: CustomTable = []
    tbody: CustomTable = []
    tfoot: CustomTable = []
    previous_row: List[CustomCell] = []

    class Config:
        arbitrary_types_allowed = True

    @staticmethod
    def parse_table(html: str) -> CustomTableWithHeaderOptional | None:
        """Parse an HTML table into a CustomTable object. Assuming a single table in the provided HTML string."""
        try:
            p = HTMLTableParser()
            return p.custom_parse_html(html)[0]
        except ValueError as e:
            logging.error(f"Error while parsing table: {e}")
            return None

    @staticmethod
    def is_valid_table(html: str) -> bool:
        p = HTMLTableParser()
        return p.custom_parse_html(html)[1]

    @staticmethod
    def parse_and_clean_table(html: str) -> CustomTableWithHeaderOptional | None:
        """Parse an html table into CustomTable object."""
        p = HTMLTableParser()
        try:
            table = p.custom_parse_html(html)[0]
            table = p.delete_and_reset_columns_and_rows(table)

            if len(p.thead) > 0:
                table.max_column_header_row = len(p.thead) - 1

            return table
        except ValueError as e:
            logging.error(f"Error while parsing table: {e}")
            return None

    def delete_and_reset_columns_and_rows(
        self, table: CustomTableWithHeaderOptional, consider_headers: bool = False
    ) -> CustomTableWithHeaderOptional:
        table = self.delete_nan_columns_and_rows(table, consider_headers)
        table = self.delete_duplicate_columns_and_rows(table, consider_headers)
        table = self.reset_indices(table)
        return table

    def reset_indices(
        self, table: CustomTableWithHeaderOptional
    ) -> CustomTableWithHeaderOptional:
        for row_index, row in enumerate(table.table):
            for col_index, cell in enumerate(row):
                cell.row_index = row_index
                cell.col_index = col_index
        return table

    def _parse_table_part(
        self, part_tag: Literal["thead", "tbody", "tfoot"], start_index_row: int
    ) -> Tuple[CustomTable, bool]:
        """Parse the table part (thead, tbody, tfoot) of the table.
        Returns:
        - List of parsed rows
        - Boolean indicating if all rows are valid and no inconsistencies had to be fixed
        """
        if not self.bs4_table:
            raise ValueError("No table existant in the provided HTML.")
        part_parsed = []

        part_bs4 = self.bs4_table.find(part_tag)
        all_rows_valid = True

        # if there is no body tag in the table, the whole table is considered as the body
        if not part_bs4 and part_tag == "tbody":
            part_bs4 = self.bs4_table

        if part_bs4:
            for row_index, row in enumerate(part_bs4.find_all("tr", recursive=False)):  # type: ignore
                self.num_rows += 1
                parsed_row = self._parse_row(row, start_index_row + row_index)
                part_parsed.append(parsed_row[0])
                if not parsed_row[1]:
                    all_rows_valid = False
        return part_parsed, all_rows_valid

    def custom_parse_html(
        self, html: str
    ) -> Tuple[CustomTableWithHeaderOptional, bool]:
        """Parse the HTML table into a CustomTable object.
        Returns:
        - CustomTable object
        - Boolean indicating if all rows are valid and no inconsistencies had to be fixed
        """

        soup = BeautifulSoup(html, "html.parser")
        # Find table
        bs4_table = soup.find("table")
        if not bs4_table:
            raise ValueError("No table found in the provided HTML.")
        elif isinstance(bs4_table, NavigableString):
            raise ValueError("Table is not a tag.")
        else:
            self.bs4_table = bs4_table

        self.thead, thead_valid = self._parse_table_part("thead", start_index_row=0)
        self.tbody, tbody_valid = self._parse_table_part(
            "tbody", start_index_row=len(self.thead)
        )
        self.tfoot, tfoot_valid = self._parse_table_part(
            "tfoot", start_index_row=len(self.thead) + len(self.tbody)
        )

        all_rows_valid = thead_valid and tbody_valid and tfoot_valid

        def _row_is_all_th(row: List[CustomCell]):
            """Check if a row contains only <th> elements."""
            return all(cell.tag_name == "th" for cell in row)

        if len(self.thead) == 0:
            # The table has no <thead>. Move the top all-<th> rows from
            # body_rows to header_rows. (This is a common case because many
            # tables in the wild have no <thead> or <tfoot>
            while self.tbody and _row_is_all_th(self.tbody[0]):
                self.thead.append(self.tbody.pop(0))

        self.table = self.thead + self.tbody + self.tfoot
        return (
            CustomTableWithHeaderOptional(table=self.table, raw_table=html),
            all_rows_valid,
        )

    def delete_nan_columns_and_rows(
        self, table: CustomTableWithHeaderOptional, consider_headers: bool = False
    ) -> CustomTableWithHeaderOptional:
        start_column_index = 0
        start_row_index = 0

        if consider_headers:
            start_column_index = (
                table.max_row_label_column + 1
                if table.max_row_label_column
                else start_column_index
            )
            start_row_index = (
                table.max_column_header_row + 1
                if table.max_column_header_row
                else start_row_index
            )

        plain_table = table.table
        row_index = 0
        while row_index < len(plain_table):
            row = plain_table[row_index]
            if all(
                cell.value.replace("\\n", "").strip() == ""
                for cell in row[start_column_index:]
            ):
                plain_table = self.delete_row(plain_table, row_index)
            else:
                row_index += 1

        plain_table = plain_table.copy()
        column_index = 0
        while column_index < len(transposed_table := list(zip(*plain_table))):
            column = transposed_table[column_index]
            if all(
                cell.value.replace("\\n", "").strip() == ""
                for cell in column[start_row_index:]
            ):
                plain_table = self.delete_column(plain_table, column_index)
            else:
                column_index += 1

        table.table = plain_table
        return table

    def delete_row(self, table: CustomTable, row_index: int) -> CustomTable:
        table = self._update_rowspan(table, row_index)
        del table[row_index]
        return table

    def delete_column(self, table: CustomTable, column_index: int) -> CustomTable:
        table = self._update_colspan(table, column_index)
        for row in table:
            del row[column_index]
        return table

    def delete_empty_columns_and_rows(
        self, table: CustomTable, consider_headers: bool = False
    ) -> CustomTable:
        # find duplicate rows
        for row_index, row in enumerate(table):
            if all(cell.value.replace("\\n", "").strip() == "" for cell in row):
                table = self.delete_row(table, row_index)

        for column_index, column in enumerate(zip(*table)):
            if all(cell.value.replace("\\n", "").strip() == "" for cell in column):
                table = self.delete_column(table, column_index)
        return table

    def delete_duplicate_columns_and_rows(
        self, table: CustomTableWithHeaderOptional, consider_headers: bool = False
    ) -> CustomTableWithHeaderOptional:

        start_column_index = -1
        start_row_index = -1

        if consider_headers:
            start_column_index = (
                table.max_row_label_column
                if table.max_row_label_column
                else start_column_index
            )
            start_row_index = (
                table.max_column_header_row
                if table.max_column_header_row
                else start_row_index
            )

        plain_table = table.table
        # find duplicate rows
        row_index = 0
        while row_index + 1 < len(plain_table):
            row = plain_table[row_index]

            if row_index + 1 < len(plain_table) and all(
                cell.value == plain_table[row_index + 1][i].value
                for i, cell in enumerate(row)
                if i > start_column_index
            ):
                plain_table = self.delete_row(plain_table, row_index)
            else:
                row_index += 1

        # find duplicate columns
        column_index = 0
        while column_index + 1 < len(transposed_table := list(zip(*plain_table))):
            column = transposed_table[column_index]
            if all(
                column[i].value == transposed_table[column_index + 1][i].value
                for i in range(len(column))
                if i > start_row_index
            ):
                plain_table = self.delete_column(plain_table, column_index)
            else:
                column_index += 1
        table.table = plain_table
        return table

    def _update_colspan(self, table: CustomTable, column_index: int) -> CustomTable:
        for row_index, row in enumerate(table):
            try:
                cell = row[column_index]
            except IndexError:
                raise ValueError("Column index out of range.")
            span_item = cell.colspan
            # update colspan for previous cells
            for i in range(1, span_item[0] + 1):
                previous_span = table[row_index][column_index - i].colspan
                previous_span_new = (
                    previous_span[0],
                    max(0, previous_span[1] - 1),
                )
                table[row_index][column_index - i].colspan = previous_span_new

            # update colspan for following cells
            for i in range(1, span_item[1] + 1):
                next_span = table[row_index][column_index + i].colspan
                next_span_new = (
                    max(0, next_span[0] - 1),
                    next_span[1],
                )
                table[row_index][column_index + i].colspan = next_span_new
        return table

    def _update_rowspan(self, table: CustomTable, row_index: int) -> CustomTable:
        for column_index, cell in enumerate(table[row_index]):
            span_item = cell.rowspan
            # update rowspan for previous cells
            for i in range(1, span_item[0] + 1):
                previous_span = table[row_index - i][column_index].rowspan
                previous_span_new = (
                    previous_span[0],
                    max(0, previous_span[1] - 1),
                )
                table[row_index - i][column_index].rowspan = previous_span_new

            # update rowspan for following cells
            for i in range(1, span_item[1] + 1):
                next_span = table[row_index + i][column_index].rowspan
                next_span_new = (
                    max(0, next_span[0] - 1),
                    next_span[1],
                )
                table[row_index + i][column_index].rowspan = next_span_new
        return table

    def _parse_row(self, row, row_index) -> Tuple[List[CustomCell], bool]:
        is_valid_row = True

        parsed_row = []
        col_index = 0
        for cell in row.find_all(["td", "th"]):
            value = " ".join(cell.stripped_strings)

            # col and rowspans refer to the number of covered cells (before, after) the current cell
            # e.g. if a cell has a colspan of 2, the colspan tuple will be (0, 1)
            colspan = cell.attrs.get("colspan")
            colspan = (0, int(colspan) - 1) if colspan else (0, 0)
            rowspan = cell.attrs.get("rowspan")
            rowspan = (0, int(rowspan) - 1) if rowspan else (0, 0)

            # update column and row count based on the colspan and rowspan
            # case if there is a rowspan in the previous row spanning over the current cell
            row_span_cells = self.get_row_span_cells(col_index, row_index)
            parsed_row.extend(row_span_cells)
            col_index += len(row_span_cells)

            # append current cell
            new_cell = CustomCell(
                value=value,
                colspan=colspan,
                rowspan=rowspan,
                row_index=row_index,
                col_index=col_index,
                tag_name=cell.name,
            )

            parsed_row.append(new_cell)
            col_index += 1

            # append cells for column span of corresponding new cell
            column_span_cells = self.get_column_span_cells(new_cell)
            parsed_row.extend(column_span_cells)
            col_index += len(column_span_cells)

        # if there are inconsistencies within the table, i.e., missing cells at the end of a row
        # assuming that the first row is complete and serves as the reference for the whole table
        if len(self.previous_row) > 0:
            while len(parsed_row) < len(self.previous_row):
                # case if rowspan covering the current column index from previous row
                row_span_cells = self.get_row_span_cells(col_index, row_index)
                if len(row_span_cells) != 0:
                    parsed_row.extend(row_span_cells)
                    col_index += len(row_span_cells)
                # if there is no rowspan covering the current column index from previous row add empty cell
                else:
                    logging.warning(
                        "Inconsistent table: Row contains less cells than the previous row. Filling up with empty cells."
                    )
                    is_valid_row = False
                    parsed_row.append(
                        CustomCell(
                            value="",
                            colspan=(0, 0),
                            rowspan=(0, 0),
                            row_index=row_index,
                            col_index=col_index,
                        )
                    )
                    col_index += 1
            if len(parsed_row) > len(self.previous_row):
                parsed_row = parsed_row[: len(self.previous_row)]
                logging.warning(
                    "Inconsistent table: Row contains more cells than the previous row. Truncating the row."
                )
                is_valid_row = False

        # update the number of columns
        self.num_columns = max(self.num_columns, col_index + 1)

        # update the previous row
        self.previous_row = parsed_row
        return parsed_row, is_valid_row

    def get_column_span_cells(self, cell: CustomCell) -> List[CustomCell]:
        new_cells = []
        for i in range(cell.colspan[1]):
            new_cell = CustomCell(
                value=cell.value,
                colspan=(i + 1, cell.colspan[1] - i - 1),
                rowspan=cell.rowspan,
                row_index=cell.row_index,
                col_index=cell.col_index + i + 1,
                tag_name=cell.tag_name,
            )
            new_cells.append(new_cell)
        return new_cells

    def get_row_span_cells(self, col_index: int, row_index: int) -> List[CustomCell]:
        new_cells = []
        try:
            while (
                len(self.previous_row) > 0
                and col_index < len(self.previous_row)
                and self.previous_row[col_index].rowspan[1] > 0
            ):
                upper_cell = self.previous_row[col_index]
                new_cell = CustomCell(
                    value=upper_cell.value,
                    colspan=upper_cell.colspan,
                    rowspan=(upper_cell.rowspan[0] + 1, upper_cell.rowspan[1] - 1),
                    row_index=row_index,
                    col_index=col_index,
                    tag_name=upper_cell.tag_name,
                )
                new_cells.append(new_cell)
                col_index += 1
        except IndexError:
            raise ValueError("Malformed table: Rowspan exceeds table boundaries.")
        return new_cells

    def _expand_colspan_rowspan(
        self, table: List[List[CustomCell]]
    ) -> List[List[CustomCell]]:
        for row in table:
            index = 0
            while index < len(row):
                cell = row[index]
                if cell.colspan:
                    _, end = cell.colspan
                    # col and rowspans refer to the number of covered cells (before, after) the current cell
                    # e.g. if a cell has a colspan of 2, the colspan tuple will be (0, 1)
                    for i in range(1, end + 1):
                        row[index + i].colspan = (i, end - i)
                index += end
        return table

    def _expand_colspan(self, table: List[List[CustomCell]]) -> List[List[CustomCell]]:
        return self._expand_colspan_rowspan(table)

    def _expand_rowspan(self, table: List[List[CustomCell]]) -> List[List[CustomCell]]:
        if not all(len(row) == len(table[0]) for row in table):
            raise ValueError("All rows must have the same number of columns.")
        transposed_table = list(zip(*table))
        return list(zip(*self._expand_colspan_rowspan(transposed_table)))

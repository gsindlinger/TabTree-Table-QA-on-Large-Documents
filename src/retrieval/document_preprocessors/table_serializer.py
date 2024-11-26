from __future__ import annotations
from abc import ABC, abstractmethod
from io import StringIO
import json
import logging
from typing import List, Optional
from bs4 import BeautifulSoup
from pydantic import BaseModel
import pandas as pd

from ...model.custom_document import CustomDocument
from .preprocess_config import PreprocessConfig

END_OF_TABLE: str = r"<EOT>"
BEGINNING_OF_TABLE: str = r"<BOT>"
# since EOF Tags are always showing up behind a table,
# the split will be empty if there are still html table tags,
# which will be deleted anyway (see HTMLPreprocessor for details)
END_OF_TABLE_REGEX: str = rf"{END_OF_TABLE}|(?<=</table>)|(?<=</table\s>)"


class DataFrameWithHeader(BaseModel):

    class Config:
        arbitrary_types_allowed = True

    df: pd.DataFrame
    has_header: bool
    header_columns: Optional[List[int]] = None
    header_rows: Optional[List[int]] = None

    def set_headers(self, header_rows: List[int], header_columns: List[int]):
        self.has_header = True
        self.header_rows = header_rows
        self.header_columns = header_columns


class CustomTable(DataFrameWithHeader):
    raw_table: str


class TableSerializer(ABC, BaseModel):
    table_splitter_backup: str = r"</\s*[^>]+>\s*?"

    @classmethod
    def from_preprocess_config(cls, config: PreprocessConfig) -> TableSerializer | None:
        from .tabgraph_serializer import TabGraphSerializer

        if config.table_serialization == "html":
            return HTMLSerializer()
        elif config.table_serialization == "csv":
            return CSVSerializer()
        elif config.table_serialization == "tsv":
            return TSVSerializer()
        elif config.table_serialization == "df-loader":
            return DFLoaderSerializer()
        elif config.table_serialization == "json-records":
            return JSONSerializerRecords()
        elif config.table_serialization == "json-split":
            return JSONSerializerSplit()
        elif config.table_serialization == "json-index":
            return JSONSerializerIndex()
        elif config.table_serialization == "markdown":
            return MarkdownSerializer()
        elif config.table_serialization == "text":
            return TextSerializer()
        elif config.table_serialization == "text-bullet-points":
            return TextSerializerBulletPoints()
        elif config.table_serialization == "list-item":
            return ListItemSerializer()
        elif config.table_serialization == "matrix":
            return MatrixSerializer()
        elif config.table_serialization == "none":
            return None
        elif config.table_serialization == "tabgraph":
            return TabGraphSerializer()
        else:
            raise ValueError(
                f"Table serialization type {config.table_serialization} is not supported"
            )

    def serialize_table(self, table: str) -> str:
        logging.info("Start serializing table")
        df_table = self.table_str_to_df(table)
        if not df_table:
            return ""
        else:
            return self.df_to_serialized_string(df_table)

    def table_str_to_df(self, table: str) -> CustomTable | None:
        df_table = self.load_table_to_df(table)
        df_table = self.delete_nan_columns_and_rows(df_table)
        if not df_table:
            return None

        df_table = self.delete_duplicate_columns_and_rows(df_table)
        return self.replace_nan_values(df_table, "")

    def delete_duplicate_columns_and_rows(
        self, custom_table: CustomTable
    ) -> CustomTable:
        df = custom_table.df
        df = df.drop_duplicates()
        df = df.T.drop_duplicates().T
        return CustomTable(
            df=df,
            has_header=custom_table.has_header,
            raw_table=custom_table.raw_table,
        )

    def replace_nan_values(self, custom_table: CustomTable, value: str) -> CustomTable:
        df = custom_table.df
        df = df.fillna(value)
        return CustomTable(
            df=df,
            has_header=custom_table.has_header,
            raw_table=custom_table.raw_table,
        )

    def delete_nan_columns_and_rows(
        self, custom_table: CustomTable
    ) -> CustomTable | None:
        df = custom_table.df
        logging.info(f"Original shape of the Table: {df.shape}")
        df = df.dropna(axis=0, how="all")
        df = df.dropna(axis=1, how="all")
        logging.info(
            f"Shape of the Table after dropping NaN columns and rows: {df.shape}"
        )
        if df.empty:
            return None
        else:
            # # Check if index is numeric and reset if so
            # if pd.api.types.is_numeric_dtype(df.index):
            #     df.reset_index(drop=True, inplace=True)

            # # Check if columns (header) are numeric and reset if so
            # if all(str(col).isdigit() for col in df.columns):
            #     df.columns = range(len(df.columns))  # Rename columns from 0 to n
            return CustomTable(
                df=df,
                has_header=custom_table.has_header,
                raw_table=custom_table.raw_table,
            )

    @abstractmethod
    def df_to_serialized_string(self, df_with_header: CustomTable) -> str:
        pass

    def load_table_to_df(self, table: str) -> CustomTable:
        soup = BeautifulSoup(table, "html.parser")
        has_thead = soup.find(["thead", "th"]) is not None

        tables = pd.read_html(StringIO(table), header=0 if has_thead else None)
        if not tables:
            raise ValueError("Could not load table to DataFrame")
        elif len(tables) > 1:
            raise ValueError("More than one table found in the table string")

        # if "Total operation and maintenance expenses" is within the tables[0] print the table
        search_string = "John C. Griffith"
        if search_string in tables[0].to_string():
            # print corresponding column of the table with the string
            matching_columns = [
                col
                for col in tables[0].columns
                if tables[0][col]
                .astype(str)
                .str.contains(search_string, na=False)
                .any()
            ]
            if matching_columns:
                for col in matching_columns:
                    print(f"The string '{search_string}' is found in column: {col}")
                    print(tables[0][col].to_list())
        return CustomTable(df=tables[0], has_header=has_thead, raw_table=table)

    def serialize_tables_in_document(self, document: CustomDocument) -> str:
        serialized_docs = []
        if not document.splitted_content:
            raise ValueError("Document does not have any splitted content")

        tables = [
            doc.model_dump() for doc in document.splitted_content if doc.type == "table"
        ]
        with open("./data/tables.json", "w") as f:
            f.write(json.dumps(tables, indent=2))

        for content in document.splitted_content:
            if content.type == "table":
                new_content = self.serialize_table(content.content)
                content.content = new_content
                new_content = (
                    f"{new_content}{END_OF_TABLE}" if new_content.strip() != "" else ""
                )
            else:
                new_content = (
                    content.content + " " if content.content.strip() != "" else ""
                )
                content.content = new_content

            serialized_docs.append(new_content)

        # only consider sentences / content items which are longer than 2 characters (e.g. filter out page numbering)
        serialized_docs_filtered = [
            doc for doc in serialized_docs if len(doc.strip()) > 3
        ]
        return "".join(serialized_docs_filtered)


class DFLoaderSerializer(TableSerializer):
    def df_to_serialized_string(self, df_with_header: CustomTable) -> str:
        df = df_with_header.df
        return df.to_string(index=False)


class HTMLSerializer(TableSerializer):
    table_splitter_backup: str = r"</\s*[^>]+>\s*?"

    def df_to_serialized_string(self, df_with_header: CustomTable) -> str:
        df = df_with_header.df
        df_html = df.to_html(header=df_with_header.has_header, index=False, na_rep="")
        soup = BeautifulSoup(df_html, "html.parser")
        for tag in soup.find_all(True):
            tag.attrs = {}
        return str(soup)


class CSVSerializer(TableSerializer):
    table_splitter_backup: str = r"\n"
    index: bool = True

    def df_to_serialized_string(
        self,
        df_with_header: CustomTable,
    ) -> str:
        df = df_with_header.df
        return df.to_csv(header=df_with_header.has_header, index=self.index, sep=";")


class TSVSerializer(TableSerializer):
    table_splitter_backup: str = r"\n"

    def df_to_serialized_string(self, df_with_header: CustomTable) -> str:
        df = df_with_header.df
        return df.to_csv(header=df_with_header.has_header, index=True, sep="\t")


class JSONSerializerRecords(TableSerializer):
    table_splitter_backup: str = r"\},\s"

    def df_to_serialized_string(self, df_with_header: CustomTable) -> str:
        df = df_with_header.df
        records_dict = df.to_dict(orient="records")
        # get rid of null values for each item in records
        records = []
        for record in records_dict:
            record = {k: v for k, v in record.items() if pd.notnull(v) and v != ""}
            if len(record) > 0:
                records.append(record)

        if len(records) == 0:
            return ""
        else:
            return json.dumps(records, indent=None)


class JSONSerializerSplit(TableSerializer):
    table_splitter_backup: str = r"\},\s"

    def df_to_serialized_string(self, df_with_header: CustomTable) -> str:
        df = df_with_header.df
        return df.to_json(orient="split")


class JSONSerializerIndex(TableSerializer):
    table_splitter_backup: str = r"\},\s"

    def df_to_serialized_string(self, df_with_header: CustomTable) -> str:
        df = df_with_header.df
        return df.to_json(orient="index")


class MatrixSerializer(TableSerializer):
    table_splitter_backup: str = r"],"

    def df_to_serialized_string(self, df_with_header: CustomTable) -> str:
        df = df_with_header.df
        return df.to_json(orient="values")


class MarkdownSerializer(TableSerializer):
    table_splitter_backup: str = r"\n"

    def df_to_serialized_string(self, df_with_header: CustomTable) -> str:
        df = df_with_header.df
        return df.to_markdown(index=True, missingval="")


class TextSerializer(TableSerializer):
    table_splitter_backup: str = r"\n"

    def df_to_serialized_string(self, df_with_header: CustomTable) -> str:
        df = df_with_header.df
        text_template = []
        for _, row in df.iterrows():
            row_text = [f"The {col} is {row[col]}." for col in df.columns]
            text_template.append(" ".join(row_text))
        return "\n".join(text_template)


class TextSerializerBulletPoints(TableSerializer):
    table_splitter_backup: str = r"\n"

    def df_to_serialized_string(self, df_with_header: CustomTable) -> str:
        df = df_with_header.df
        bullet_point_text = []
        for _, row in df.iterrows():
            row_text = [f"{col} is {row[col]}" for col in df.columns]
            bullet_point_text.append("- " + ". ".join(row_text) + ".")
        return "\n".join(bullet_point_text)


class ListItemSerializer(TableSerializer):
    table_splitter_backup: str = r"\n"

    def df_to_serialized_string(self, df_with_header: CustomTable) -> str:
        df = df_with_header.df
        text_template = []
        for _, row in df.iterrows():
            row_text = [f"The {col} is {row[col]}." for col in df.columns]
            text_template.append(" ".join(row_text))
        return "\n".join(text_template)
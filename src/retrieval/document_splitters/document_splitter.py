from __future__ import annotations
from abc import ABC, abstractmethod
import logging
from typing import List, Optional, Tuple
from langchain_text_splitters import TextSplitter
from pydantic import BaseModel


from ...retrieval.embeddings.custom_embeddings import CustomEmbeddings
from ..document_preprocessors.table_serializer import TableSerializer
from ..document_preprocessors.preprocess_config import PreprocessConfig
from ...config.config import Config
from ...model.custom_document import CustomDocument, FullMetadata, SplitContent
from langchain_core.embeddings import Embeddings


class DocumentSplitter(ABC, BaseModel):
    text_splitter: Optional[TextSplitter] = None
    chunk_size: Optional[int] = None
    name: str

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def from_config(
        cls,
        embeddings: CustomEmbeddings,
        preprocess_config: PreprocessConfig,
    ) -> DocumentSplitter:
        match Config.indexing.chunking_strategy:
            case "recursive-character":
                from .sec_filing_splitter import SECFilingSplitter

                return SECFilingSplitter(
                    name=Config.indexing.chunking_strategy, chunk_size=500
                )
            case "recursive-character-html":
                from .sec_filing_splitter import SECFilingSplitterHTML

                return SECFilingSplitterHTML(
                    name=Config.indexing.chunking_strategy, chunk_size=8000
                )

            case "semantic":
                from .sec_filing_splitter import SECFilingSplitterSemantic

                table_serializer = TableSerializer.from_preprocess_config(
                    preprocess_config
                )

                return SECFilingSplitterSemantic(
                    name=Config.indexing.chunking_strategy,
                    embeddings=embeddings,
                    chunk_size=10000,
                    table_serializer=table_serializer,
                    preprocess_config=preprocess_config,
                )

            case _:
                raise ValueError(f"Unknown mode: {Config.indexing.chunking_strategy}")

    def split_document(
        self, document: CustomDocument, ignore_tables_for_embeddings: bool = False
    ) -> List[CustomDocument]:
        if document.splitted_content:
            splitted_text = self.split_text(document.splitted_content)
            splitted_documents = [
                CustomDocument(
                    page_content=chunk.content, # type: ignore
                    metadata=FullMetadata(doc_id=document.metadata.doc_id, chunk_id=f"{document.metadata.doc_id}_{i}", additional_metadata={"table_string": chunk.original_content, "contains_table": chunk.type == "table"}) # type: ignore
                )
                for i, chunk in enumerate(splitted_text)
            ]
            return splitted_documents
    
        else:
            splitted_text = self.split_text(document.page_content)
            return [
                CustomDocument(
                    page_content=chunk,
                    metadata=document.extend_metadata(chunk_id=i), # type: ignore
                    
                )
                for i, chunk in enumerate(splitted_text)
            ]

    def split_documents(
        self,
        documents: List[CustomDocument],
        ignore_tables_for_embeddings: bool = False,
    ) -> List[CustomDocument]:
        """ Returns a flattened list of all chunks of all documents """
        splitted_documents = []
        for document in documents:
            splitted_documents.extend(
                self.split_document(
                    document=document,
                    ignore_tables_for_embeddings=ignore_tables_for_embeddings,
                )
            )

        logging.info(
            f"Split {len(documents)} documents into {len(splitted_documents)} chunks"
        )
        return splitted_documents

    def split_text(self, text: str | List[SplitContent]) -> List[str] | List[SplitContent]:
        from .sec_filing_splitter import SECFilingSplitterSemantic
        if self.text_splitter is None:
            raise ValueError("Text splitter is not initialized")
        elif isinstance(text, list) and not isinstance(self, SECFilingSplitterSemantic):
            return self.text_splitter.split_text(" ".join([chunk.content for chunk in text]))
        elif isinstance(self, SECFilingSplitterSemantic):
            split_content = self.split_text_to_list(text) # type: ignore
            return split_content
        
        return self.text_splitter.split_text(text) # type: ignore
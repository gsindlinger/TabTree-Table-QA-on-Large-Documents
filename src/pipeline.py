from __future__ import annotations
from abc import ABC, abstractmethod
import logging
from operator import itemgetter
from typing import Iterator, List, Literal, Tuple
from pydantic import BaseModel
from tenacity import RetryCallState, retry, stop_after_attempt, wait_random_exponential

from .retrieval.document_preprocessors.table_serializer import TableSerializer
from .retrieval.document_preprocessors.preprocess_config import PreprocessConfig

from .retrieval.document_preprocessors.table_parser.custom_table import (
    CustomTableWithHeaderOptional,
)
from .generation.abstract_llm import LLM
from .model.custom_document import CustomDocument
from .retrieval.retriever import QdrantRetriever
from .retrieval.qdrant_store import QdrantVectorStore
from .config import Config


from langchain_core.runnables.base import RunnableSerializable
from langchain_core.runnables import RunnablePassthrough
from langchain_core.language_models.base import BaseLanguageModel
from langchain.retrievers.multi_query import MultiQueryRetriever
from langchain.prompts import PromptTemplate
from langchain_core.output_parsers.string import StrOutputParser


class AbstractPipeline(ABC, BaseModel):
    template: str
    prompt: PromptTemplate
    output_parser: StrOutputParser
    llm: BaseLanguageModel
    llm_chain: RunnableSerializable
    error_count: int = 0

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    @abstractmethod
    def from_config(cls) -> AbstractPipeline:
        pass


class TableQAPipeline(AbstractPipeline):
    @classmethod
    def from_config(
        cls,
    ) -> TableQAPipeline:
        template = Config.text_generation.prompt_template_table_qa
        llm = LLM.from_config()
        prompt = PromptTemplate(
            input_variables=["table", "question"],
            template=template,
        )
        output_parser = StrOutputParser()

        llm_chain = (prompt | llm | output_parser).with_config({"tags": ["table-qa"]})
        return cls(
            template=template,
            llm=llm,
            prompt=prompt,
            output_parser=output_parser,
            llm_chain=llm_chain,
        )

    def invoke(self, table_title: str | None, table: str, question: str) -> str:
        if table_title:
            table_title = f"\nTable title: {table_title}\n"
        else:
            table_title = ""

        try:
            return self.llm_chain.invoke(
                input={"table_title": table_title, "table": table, "question": question}
            )
        except Exception as e:
            logging.error(f"Error invoking pipeline: {e}")
            self.error_count += 1
            return "Answer: None"


class RAGPipeline(AbstractPipeline):
    table_serializer: TableSerializer | None
    retriever: MultiQueryRetriever | QdrantRetriever

    class Config:
        arbitrary_types_allowed = True

    @classmethod
    def from_config(
        cls,
        vector_store: QdrantVectorStore,
        retriever_num_documents: int,
        preprocess_config: PreprocessConfig,
    ) -> RAGPipeline:
        if preprocess_config.table_serialization_related_tables:
            table_serializer = TableSerializer.from_table_serializer_preprocess_config(
                preprocess_config.table_serialization_related_tables
            )
        else:
            table_serializer = TableSerializer.from_preprocess_config(preprocess_config)

        retriever = QdrantRetriever(
            vector_store, retriever_num_documents=retriever_num_documents
        )
        if preprocess_config.table_serialization.include_related_tables:
            template = Config.text_generation.prompt_template_rag
        else:
            template = Config.text_generation.prompt_template_without_related_tables

        llm = LLM.from_config()
        prompt = PromptTemplate(
            input_variables=["context", "related_tables", "question"],
            template=template,
        )
        output_parser = StrOutputParser()

        # retriever = MultiQueryRetriever.from_llm(retriever=retriever_base, llm=llm)
        retrieval_chain = (
            retriever
            | (
                lambda docs: retriever.format_docs(
                    docs=docs,
                    table_serializer=table_serializer,
                    include_related_tables=preprocess_config.table_serialization.include_related_tables,
                )
            )
            | (lambda result: {"context": result[0], "related_tables": result[1]})
        )

        # llm_chain = retrieval_chain
        llm_chain = (
            {
                "context": retrieval_chain | itemgetter("context"),
                "related_tables": retrieval_chain | itemgetter("related_tables"),
                "question": RunnablePassthrough(),
            }
            | prompt
            | llm
            | output_parser
        ).with_config({"tags": ["rag"]})
        return cls(
            retriever=retriever,
            template=template,
            llm=llm,
            prompt=prompt,
            output_parser=output_parser,
            llm_chain=llm_chain,  # type: ignore
            table_serializer=table_serializer,
        )

    def retrieve(self, question: str = "What is love?") -> List[CustomDocument]:
        if not self.retriever:
            raise ValueError("Retriever is not set")
        docs = self.retriever.invoke(question)
        return CustomDocument.docs_to_custom_docs(docs)

    @staticmethod
    def log_before_retry(retry_state: RetryCallState):
        logging.warning(
            f"Retrying due to error: {retry_state.outcome.exception()}. "
            f"Attempt #{retry_state.attempt_number}, "
            f"Next try in {retry_state.next_action.sleep:.2f} seconds."
        )

    @retry(
        wait=wait_random_exponential(min=1, max=60),
        stop=stop_after_attempt(6),
        before_sleep=log_before_retry,
    )
    def invoke_with_retry(self, question: str = "What is love?") -> str:
        response = self.llm_chain.invoke(question)
        logging.info(f"Question: {question}")
        logging.info(f"Answer: {response}")
        return response

    def invoke(self, question: str = "What is love?") -> str:
        try:
            response = self.invoke_with_retry(question)
        except Exception as e:
            logging.error(f"Error invoking pipeline: {e}")
            self.error_count += 1
            response = "Answer: None"
        return response

    def stream(self, question: str = "What is love?") -> Iterator:
        return self.llm_chain.stream(question)


class QuestionDomainPipeline(AbstractPipeline):
    @classmethod
    def from_config(
        cls,
    ) -> QuestionDomainPipeline:
        template = Config.text_generation.prompt_template_question_domain
        llm = LLM.from_config()
        prompt = PromptTemplate(
            input_variables=["question", "table"],
            template=template,
        )
        output_parser = StrOutputParser()

        llm_chain = (prompt | llm | output_parser).with_config(
            {"tags": ["question-domain"]}
        )
        return cls(
            template=template,
            llm=llm,
            prompt=prompt,
            output_parser=output_parser,
            llm_chain=llm_chain,
        )

    def predict_domain(self, question: str, table: str) -> str:
        return self.llm_chain.invoke(input={"question": question, "table": table})


class QuestionCategoryPipeline(AbstractPipeline):
    @classmethod
    def from_config(
        cls,
    ) -> QuestionCategoryPipeline:
        template = Config.text_generation.prompt_template_question_category
        llm = LLM.from_config()
        prompt = PromptTemplate(
            input_variables=["question", "table"],
            template=template,
        )
        output_parser = StrOutputParser()

        llm_chain = (prompt | llm | output_parser).with_config(
            {"tags": ["question-category"]}
        )
        return cls(
            template=template,
            llm=llm,
            prompt=prompt,
            output_parser=output_parser,
            llm_chain=llm_chain,
        )

    def predict_category(self, question: str, table: str) -> str:
        return self.llm_chain.invoke(input={"question": question, "table": table})


class TableSummaryPipeline(AbstractPipeline):
    @classmethod
    def from_config(
        cls,
    ) -> TableSummaryPipeline:
        template = Config.text_generation.prompt_template_table_summary
        llm = LLM.from_config()
        prompt = PromptTemplate(
            input_variables=["table", "preceding_sentence"],
            template=template,
        )
        output_parser = StrOutputParser()

        llm_chain = (prompt | llm | output_parser).with_config(
            {"tags": ["table-summary"]}
        )
        return cls(
            template=template,
            llm=llm,
            prompt=prompt,
            output_parser=output_parser,
            llm_chain=llm_chain,
        )

    def predict_table_summary(self, table: str, preceding_sentence: str) -> str:
        return self.llm_chain.invoke(
            input={"table": table, "preceding_sentence": preceding_sentence}
        )


class TableHeaderRowsPipeline(AbstractPipeline):
    @classmethod
    def from_config(
        cls,
    ) -> TableHeaderRowsPipeline:
        # template = Config.tabtree.prompt_template_header_detection
        # template = Config.tabtree.prompt_template_header_detection_1_range
        template = Config.tabtree.prompt_template_header_detection_full_table
        llm = LLM.from_config()
        prompt = PromptTemplate(
            input_variables=["table"],
            template=template,
        )
        output_parser = StrOutputParser()

        llm_chain = (prompt | llm | output_parser).with_config(
            {"tags": ["header-detection"]}
        )
        return cls(
            template=template,
            llm=llm,
            prompt=prompt,
            output_parser=output_parser,
            llm_chain=llm_chain,
        )

    def predict_headers(
        self, custom_table: CustomTableWithHeaderOptional
    ) -> Tuple[int, int]:
        """Get the row and column headers of the table.
        First item of return tuple refers to max column header row and second item refers to max row label column.
        """
        max_column_header_row = self.predict_headers_single(
            full_table=custom_table.to_html(), table_df=custom_table, mode="row"
        )
        max_row_label_column = self.predict_headers_single(
            full_table=custom_table.to_html(), table_df=custom_table, mode="column"
        )

        if max_column_header_row == len(custom_table):
            logging.warning(
                "Header detection provided column header rows for full table, reducing max column header row by 1"
            )
        if max_row_label_column == len(custom_table[0]):
            logging.warning(
                "Header detection provided row label columns for full table, reducing max row label column by 1"
            )
            max_row_label_column -= 1
        return max_column_header_row, max_row_label_column

    def predict_headers_single(
        self,
        full_table: str,
        table_df: CustomTableWithHeaderOptional | None,
        mode: Literal["row", "column"] = "row",
    ) -> int:
        index = -1
        while True:
            if not table_df:
                break

            previous_items = []
            next_items = []
            second_previous_items = []
            second_next_items = []

            match mode:
                case "row":
                    if index + 2 == table_df.rows:
                        break

                    items = table_df.get_row(index + 1)
                    if index > 0:
                        second_previous_items = table_df.get_row(index - 1)
                    if index > -1:
                        previous_items = table_df.get_row(index)
                    if index + 3 < table_df.rows:
                        next_items = table_df.get_row(index + 2)
                    if index + 4 < table_df.rows:
                        second_next_items = table_df.get_row(index + 3)
                case "column":
                    if index + 2 == table_df.columns:
                        break

                    items = table_df.get_column(index + 1)
                    if index > 0:
                        second_previous_items = table_df.get_column(index - 1)
                    if index > -1:
                        previous_items = table_df.get_column(index)
                    if index + 3 < table_df.columns:
                        next_items = table_df.get_column(index + 2)
                    if index + 4 < table_df.columns:
                        second_next_items = table_df.get_column(index + 3)

            row_description = Config.tabtree.header_description
            column_description = Config.tabtree.label_description
            negative_description = Config.tabtree.negative_description

            if not self.predict_headers_single_llm(
                table=full_table,
                line=CustomTableWithHeaderOptional.print_line_with_span(items, mode),
                mode=mode,
                mode_header_name="header" if mode == "row" else "label",
                mode_description=(
                    row_description if mode == "row" else column_description
                ),
                negative_description=negative_description,
                previous_items=CustomTableWithHeaderOptional.print_line_with_span(
                    previous_items, mode
                ),
                next_items=CustomTableWithHeaderOptional.print_line_with_span(
                    next_items, mode
                ),
                second_previous_items=CustomTableWithHeaderOptional.print_line_with_span(
                    second_previous_items, mode
                ),
                second_next_items=CustomTableWithHeaderOptional.print_line_with_span(
                    second_next_items, mode
                ),
                index=index + 1,
            ):
                break

            index += 1

        if index == -1:
            response = -1
        else:
            response = index

        return response

    def predict_headers_single_llm(
        self,
        table: str,
        line: str,
        mode: str,
        mode_header_name: str,
        mode_description: str,
        negative_description: str,
        previous_items: str,
        next_items: str,
        second_previous_items: str,
        second_next_items: str,
        index: int,
    ) -> bool:
        if previous_items != "[]":
            previous_index = f"The previous {mode} looks like this: {previous_items}"
        else:
            previous_index = f"The provided {mode} is the first {mode}. Please consider this when answering the question."

        if next_items != "[]":
            next_index = f"The next {mode} looks like this: {next_items}"
        else:
            next_index = f"The provided {mode}  is the last {mode}. Please consider this when answering the question."

        if second_previous_items != "[]":
            second_previous_index = (
                f"The second previous {mode} looks like this: {second_previous_items}"
            )
        else:
            second_previous_index = ""

        if second_next_items != "[]":
            second_next_index = (
                f"The second next {mode} looks like this: {second_next_items}"
            )
        else:
            second_next_index = ""

        response = self.llm_chain.invoke(
            input={
                "table": table,
                "line": line,
                "mode": mode,
                "mode_header_name": mode_header_name,
                "mode_description": mode_description,
                "negative_description": negative_description,
                "previous_index": previous_index,
                "next_index": next_index,
                "second_previous_index": second_previous_index,
                "second_next_index": second_next_index,
                "index": index,
            },
            config={"tags": ["header-detection", mode]},
        )

        # check whether response contains yes or no
        if "yes" in response.lower():
            print("Yes")
            return True
        elif "no" in response.lower():
            print("No")
            return False
        else:
            logging.warning(f"Response does not contain yes or no: {response}")
            if index == 0:
                return True
            else:
                return False

import queue
from typing import TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.core.config import settings
from app.core.exceptions import LLMExtractionError
from app.core.logger import get_logger
from app.prompts.sales_specials import SYSTEM_PROMPT
from app.schemas.llm import OfferExtractionResponse

logger = get_logger(__name__)

SALES_SPECIALS_SYSTEM_PROMPT = SYSTEM_PROMPT

T = TypeVar("T", bound=BaseModel)


class LLMOfferExtractor:
    """Sends scraped page text to the LLM and returns structured output.

    Holds a pool of raw clients (one per API key). Each ``extract`` call checks a
    client out, binds the requested response schema for that call, runs it, and
    returns the client to the pool — so up to ``len(keys)`` extractions run in
    parallel, each on a distinct key, without exceeding one key's limit.

    The prompt and response schema are parameters so every offer type can reuse
    the same key-pool infrastructure. They default to the Sales Specials prompt +
    :class:`OfferExtractionResponse`, keeping the original behavior identical.
    """

    def __init__(self) -> None:
        keys = settings.resolved_api_keys()
        if not keys:
            raise LLMExtractionError("No Gemini API key configured.")

        self._pool: "queue.Queue[ChatOpenAI]" = queue.Queue()
        for key in keys:
            llm = ChatOpenAI(
                model=settings.gemini_model,
                api_key=key,
                base_url=settings.gemini_base_url,
                timeout=settings.gemini_timeout_seconds,
                max_retries=settings.gemini_max_retries,
                max_tokens=settings.gemini_max_tokens,
                temperature=0,
            )
            self._pool.put(llm)
        self.key_count = len(keys)

    def extract(
        self,
        body: str,
        prompt: str | None = None,
        schema: type[T] = OfferExtractionResponse,
    ) -> T:
        """Extract structured data of type ``schema`` from ``body``.

        ``prompt`` defaults to the Sales Specials system prompt and ``schema`` to
        :class:`OfferExtractionResponse` so existing callers are unaffected.
        """
        system_prompt = prompt if prompt is not None else SYSTEM_PROMPT
        truncated = body[: settings.max_body_chars]
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=truncated),
        ]
        llm = self._pool.get()
        try:
            structured = llm.with_structured_output(schema)
            result = structured.invoke(messages)
        except Exception as exc:
            logger.error("LLM structured extraction failed | error=%s", str(exc))
            raise LLMExtractionError(f"LLM extraction failed: {exc}") from exc
        finally:
            self._pool.put(llm)

        if not isinstance(result, schema):
            raise LLMExtractionError("LLM returned no structured output.")
        return result

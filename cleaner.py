from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from config import CleanerConfig

logger = logging.getLogger(__name__)


# Embedded gemma (local_llm.py) AND cloud LLM cleanup (ApiCleaner/OllamaCleaner)
# were REMOVED — GigaAM v3-e2e already punctuates and the deterministic filler
# stripper (filler.py) handles «э-э / ну» upstream. Only Noop (passthrough) and
# the local PunctuationCleaner remain. To restore the LLM path: `git checkout
# before-cuts -- cleaner.py` and re-add the build_cleaner_chain api/ollama branch.


class Cleaner(ABC):
    @abstractmethod
    def clean(self, text: str) -> str: ...


class NoopCleaner(Cleaner):
    def clean(self, text: str) -> str:
        return text


class PunctuationCleaner(Cleaner):
    """Local punctuation-restoration fallback (no LLM, no network). Backend
    lazy-loaded by `punctuation.restore` — first use may take a few seconds;
    subsequent calls are fast.
    """

    def clean(self, text: str) -> str:
        from punctuation import restore
        return restore(text)


class CleanerChain:
    def __init__(self, cleaners: list[Cleaner]) -> None:
        self._cleaners = cleaners

    def clean(self, text: str) -> tuple[str, bool]:
        """Returns (result_text, was_cleaned_by_a_real_cleaner)."""
        if not text or not text.strip():
            return text, False
        for cleaner in self._cleaners:
            name = type(cleaner).__name__
            try:
                result = cleaner.clean(text)
                cleaned = not isinstance(cleaner, NoopCleaner)
                if cleaned:
                    logger.info(f"{name}: {len(text)} → {len(result)} chars")
                return result, cleaned
            except Exception as e:
                logger.error(f"{name}: {e}", exc_info=True)

        logger.warning("All cleaners failed, returning raw text")
        return text, False


def build_cleaner_chain(configs: list[CleanerConfig],
                        punctuation_fallback: bool = True) -> CleanerChain:
    """Build the cleanup chain. Only `noop` and `punctuation` cleaner types are
    supported now (LLM cleaners removed); stale `api`/`ollama`/`local` entries
    from older configs are ignored rather than crashing.
    """
    cleaners: list[Cleaner] = []
    for cfg in configs:
        if cfg.type == "punctuation":
            cleaners.append(PunctuationCleaner())
        elif cfg.type == "noop":
            cleaners.append(NoopCleaner())
        else:
            logger.info(f"cleaner type {cfg.type!r} unsupported (LLM cleanup removed); skipping")

    # Inject PunctuationCleaner just before NoopCleaner (or at the end) unless the
    # user disabled the fallback or already added it via config.
    if punctuation_fallback and not any(isinstance(c, PunctuationCleaner) for c in cleaners):
        for i, c in enumerate(cleaners):
            if isinstance(c, NoopCleaner):
                cleaners.insert(i, PunctuationCleaner())
                break
        else:
            cleaners.append(PunctuationCleaner())

    if not cleaners:
        cleaners.append(NoopCleaner())

    return CleanerChain(cleaners)

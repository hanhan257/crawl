from __future__ import annotations

from abc import ABC, abstractmethod

from models.product import Product


class BaseCrawler(ABC):
    source: str

    @abstractmethod
    def crawl(self, **kwargs) -> list[Product]:
        raise NotImplementedError

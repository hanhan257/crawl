from .trendsi import TrendsiCrawler
from .shopbase import ShopBaseCrawler
from .generic_web import GenericWebCrawler
from .magento_graphql import MagentoGraphqlCrawler
from .redbubble import RedbubbleCrawler
from .shopify import ShopifyCrawler
from .woocommerce import WooCommerceCrawler

__all__ = [
    "ShopifyCrawler",
    "WooCommerceCrawler",
    "RedbubbleCrawler",
    "GenericWebCrawler",
    "ShopBaseCrawler",
    "TrendsiCrawler",
    "MagentoGraphqlCrawler",
]

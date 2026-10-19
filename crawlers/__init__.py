from .campaign_shop import CampaignShopCrawler
from .teemill import TeemillCrawler
from .trendsi import TrendsiCrawler
from .shopbase import ShopBaseCrawler
from .generic_web import GenericWebCrawler
from .magento_graphql import MagentoGraphqlCrawler
from .fineartamerica import FineArtAmericaCrawler
from .redbubble import RedbubbleCrawler
from .shopify import ShopifyCrawler
from .shopify_hydrogen import ShopifyHydrogenCrawler
from .woocommerce import WooCommerceCrawler

__all__ = [
    "ShopifyCrawler",
    "ShopifyHydrogenCrawler",
    "WooCommerceCrawler",
    "RedbubbleCrawler",
    "FineArtAmericaCrawler",
    "GenericWebCrawler",
    "ShopBaseCrawler",
    "CampaignShopCrawler",
    "TeemillCrawler",
    "TrendsiCrawler",
    "MagentoGraphqlCrawler",
]

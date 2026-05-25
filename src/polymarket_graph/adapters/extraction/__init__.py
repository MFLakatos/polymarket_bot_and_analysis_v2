"""Extraction adapters: Polymarket Data API + CLOB API clients."""
from polymarket_graph.adapters.extraction.base import BaseApiClient, TradeSource
from polymarket_graph.adapters.extraction.clob_api import ClobApiClient
from polymarket_graph.adapters.extraction.gamma_api import GammaApiClient
from polymarket_graph.adapters.extraction.hybrid import HybridTradeSource

__all__ = ["BaseApiClient", "ClobApiClient", "GammaApiClient", "HybridTradeSource", "TradeSource"]

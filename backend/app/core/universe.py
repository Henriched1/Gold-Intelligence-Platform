"""Provider-neutral market universe.

An enabled instrument is allowed by the product schema; this does NOT imply
that a live feed is connected or that the instrument is currently tradable.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str
    asset_class: str
    base: str | None = None
    quote: str | None = None
    enabled: bool = True


INSTRUMENTS = (
    *(Instrument(pair, "FX", pair[:3], pair[3:]) for pair in (
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD",
        "NZDUSD", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY", "EURAUD",
        "EURCHF", "GBPAUD", "GBPCAD", "CADJPY", "CHFJPY", "AUDCAD",
        "NZDCAD", "EURNZD",
    )),
    Instrument("XAUUSD", "METALS", "XAU", "USD"),
    Instrument("XAGUSD", "METALS", "XAG", "USD"),
    Instrument("USOIL", "ENERGY"),
    Instrument("UKOIL", "ENERGY"),
    Instrument("US500", "INDEX"),
    Instrument("NAS100", "INDEX"),
    Instrument("US30", "INDEX"),
    Instrument("GER40", "INDEX"),
    Instrument("BTCUSD", "CRYPTO", "BTC", "USD"),
    Instrument("ETHUSD", "CRYPTO", "ETH", "USD"),
)

_BY_SYMBOL = {item.symbol: item for item in INSTRUMENTS}


def list_instruments(asset_class: str | None = None) -> list[dict]:
    items = INSTRUMENTS
    if asset_class:
        wanted = asset_class.strip().upper()
        items = tuple(item for item in items if item.asset_class == wanted)
    return [
        {
            "symbol": item.symbol,
            "asset_class": item.asset_class,
            "base": item.base,
            "quote": item.quote,
            "enabled": item.enabled,
            "live_data_available": False,
        }
        for item in items
    ]


def get_instrument(symbol: str) -> Instrument | None:
    return _BY_SYMBOL.get(symbol.strip().upper())

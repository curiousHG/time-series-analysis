from dataclasses import dataclass


@dataclass
class FundHolding:
    scheme_code: int
    scheme_name: str
    isin: str
    stock_name: str
    weight: float
    as_of: str

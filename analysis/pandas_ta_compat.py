"""
pandas_ta 兼容层
用 `ta` 库实现 pandas_ta 的 API, 解决 pandas_ta 安装困难问题
"""

import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from ta.trend import MACD as MACDIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import OnBalanceVolumeIndicator


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """计算 RSI, 兼容 pandas_ta.rsi() 接口"""
    indicator = RSIIndicator(close=close, window=length)
    return indicator.rsi()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """计算 MACD, 兼容 pandas_ta.macd() 接口

    Returns DataFrame with columns:
        [MACD_line, MACD_histogram, MACD_signal]
    """
    indicator = MACDIndicator(close=close, window_fast=fast, window_slow=slow, window_sign=signal)
    df = pd.DataFrame({
        f"MACD_{fast}_{slow}_{signal}": indicator.macd(),
        f"MACDh_{fast}_{slow}_{signal}": indicator.macd_diff(),
        f"MACDs_{fast}_{slow}_{signal}": indicator.macd_signal(),
    })
    return df


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """计算 ATR, 兼容 pandas_ta.atr() 接口"""
    indicator = AverageTrueRange(high=high, low=low, close=close, window=length)
    return indicator.average_true_range()


def bbands(close: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """计算布林带, 兼容 pandas_ta.bbands() 接口

    Returns DataFrame with columns:
        [BBL, BBM, BBU, ...]
    """
    indicator = BollingerBands(close=close, window=length, window_dev=std)
    df = pd.DataFrame({
        f"BBL_{length}_{std}": indicator.bollinger_lband(),
        f"BBM_{length}_{std}": indicator.bollinger_mavg(),
        f"BBU_{length}_{std}": indicator.bollinger_hband(),
    })
    return df


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """计算 OBV, 兼容 pandas_ta.obv() 接口"""
    indicator = OnBalanceVolumeIndicator(close=close, volume=volume)
    return indicator.on_balance_volume()

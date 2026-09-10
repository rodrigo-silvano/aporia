"""
SplitMix64 Pseudo-Random Number Generator.
Provides exact deterministic reference streams for APORIA scientific experiments.
"""

from __future__ import annotations


class SplitMix64V1:
    def __init__(self, seed_hex: str) -> None:
        self.state = int(seed_hex, 16) & 0xFFFFFFFFFFFFFFFF

    @staticmethod
    def addSmallHex(hex_str: str, value: int) -> str:
        s = (int(hex_str, 16) + value) & 0xFFFFFFFFFFFFFFFF
        return format(s, "016x")

    def next_u64(self) -> int:
        self.state = (self.state + 0x9e3779b97f4a7c15) & 0xFFFFFFFFFFFFFFFF
        z = self.state
        z = ((z ^ (z >> 30)) * 0xbf58476d1ce4e5b9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94d049bb133111eb) & 0xFFFFFFFFFFFFFFFF
        return (z ^ (z >> 31)) & 0xFFFFFFFFFFFFFFFF

    def nextHex(self) -> str:
        return format(self.next_u64(), "016x")

    def nextBelow(self, bound: int) -> int:
        if bound <= 1:
            return 0
        return self.next_u64() % bound

    def nextUnit(self) -> float:
        return (self.next_u64() >> 11) * (1.0 / (1 << 53))

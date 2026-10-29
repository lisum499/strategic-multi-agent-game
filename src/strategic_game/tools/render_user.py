"""Shared packaged prompt paths and the stable sealed system prompt."""
from __future__ import annotations
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROMPT_DIR = BASE_DIR / "prompts" / "v2.1"
RULE_CARD_FILE = PROMPT_DIR / "20-规则卡-v2.0.md"
SYSTEM_FILE = BASE_DIR / "prompts" / "sealed" / "00-system.md"


def build_system() -> str:
    text = SYSTEM_FILE.read_text(encoding="utf-8") + "\n\n" + RULE_CARD_FILE.read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip() + "\n"

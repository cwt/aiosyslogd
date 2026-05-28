from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ParsedActivity:
    app: str
    user: str
    appcat: str = "Unknown"


class BaseActivityParser:
    def extract(self, message: str) -> Optional[ParsedActivity]:
        raise NotImplementedError


from .fortios import FortiOSParser  # noqa: E402


_ACTIVITY_PARSERS: Dict[str, type] = {
    "fortios": FortiOSParser,
}


def get_activity_parser(name: str) -> BaseActivityParser:
    parser_cls = _ACTIVITY_PARSERS.get(name)
    if parser_cls is None:
        raise ValueError(
            f"Unknown activity parser: {name}. "
            f"Available: {', '.join(_ACTIVITY_PARSERS.keys())}"
        )
    return parser_cls()

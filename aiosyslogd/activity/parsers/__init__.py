from dataclasses import dataclass


@dataclass
class ParsedActivity:
    app: str
    user: str
    appcat: str = "Unknown"


class BaseActivityParser:
    def extract(self, message: str) -> ParsedActivity | None:
        raise NotImplementedError


from .fortios import FortiOSParser

_ACTIVITY_PARSERS: dict[str, type] = {
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

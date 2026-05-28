"""FortiOS log format activity parser.

Extracts app/user/appcat from FortiOS CEF-style kv-pair log messages.

Example message:
    ... app="YouTube" user="john.doe" appcat="Video" ...
"""

import re
from typing import Optional

from . import BaseActivityParser, ParsedActivity


class FortiOSParser(BaseActivityParser):
    def extract(self, message: str) -> Optional[ParsedActivity]:
        app_match = re.search(r'\bapp="([^"]*)"', message)
        if not app_match:
            return None
        user_match = re.search(r'\buser="([^"]*)"', message)
        if not user_match:
            return None
        appcat_match = re.search(r'\bappcat="([^"]*)"', message)
        return ParsedActivity(
            app=app_match.group(1),
            user=user_match.group(1),
            appcat=appcat_match.group(1) if appcat_match else "Unknown",
        )

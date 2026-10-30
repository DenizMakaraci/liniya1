from .client import get_client
from .fast import FastPath
from .reasoner import LegalReasoner
from .slow import SlowPath
from .speaker import Speaker

__all__ = ["FastPath", "LegalReasoner", "SlowPath", "Speaker", "get_client"]

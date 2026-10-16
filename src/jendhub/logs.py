import json
import logging
from typing import override

PLAIN = "%(message)s"


class JsonFormatter(logging.Formatter):
    @override
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging(*, structured: bool) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if structured else logging.Formatter(PLAIN))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)

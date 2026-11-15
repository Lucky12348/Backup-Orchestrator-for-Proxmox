from pydantic import BaseModel


class CalendarFeedRead(BaseModel):
    enabled: bool
    # Full subscription URL (contains the token); None when disabled.
    url: str | None = None

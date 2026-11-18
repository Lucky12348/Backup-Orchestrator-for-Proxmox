from datetime import datetime
from typing import Any

from pydantic import ConfigDict

from app.schemas.base import UTCDateTimeModel


class ActivityEventRead(UTCDateTimeModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    category: str
    action: str
    status: str
    summary: str
    details: dict[str, Any] | None = None

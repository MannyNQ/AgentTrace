"""The base of every record a store returns and the API serves."""

from pydantic import BaseModel, ConfigDict


class Record(BaseModel):
    model_config = ConfigDict(frozen=True)

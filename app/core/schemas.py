"""Shared response-model base.

Everything the API returns uses camelCase on the wire (as in the assignment examples)
while staying snake_case in Python.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

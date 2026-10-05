"""Bounded settings and explicit item-use feed extension."""
from datetime import datetime
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

CATALOG = json.loads(Path(__file__).with_name('item-catalog.json').read_text(encoding='utf-8'))
ITEMS = {item['id']: item for item in CATALOG['items']}
FACTIONS = ('Lonestar', 'Valkyra', 'Manticore')


def faction_name(value):
    if not isinstance(value, str):
        return None
    return {'blu': 'Lonestar', 'lonestar': 'Lonestar', 'red': 'Valkyra', 'valkyra': 'Valkyra',
            'grn': 'Manticore', 'manticore': 'Manticore'}.get(value.strip().casefold())


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class FactionLimits(ClosedModel):
    Lonestar: StrictInt = Field(default=0, ge=0, le=1000)
    Valkyra: StrictInt = Field(default=0, ge=0, le=1000)
    Manticore: StrictInt = Field(default=0, ge=0, le=1000)


class FactionSettings(ClosedModel):
    enabled: StrictBool = False
    limits: FactionLimits = Field(default_factory=FactionLimits)
    balanceEnabled: StrictBool = True
    maxDifference: StrictInt = Field(default=2, ge=1, le=1000)
    minimumPlayers: StrictInt = Field(default=0, ge=0, le=1000)
    stableSeconds: StrictInt = Field(default=10, ge=0, le=300)
    cooldownSeconds: StrictInt = Field(default=15, ge=5, le=600)


class RestrictedItem(ClosedModel):
    itemId: str = Field(min_length=1, max_length=128)
    killCauses: list[str] = Field(default_factory=list, max_length=20)

    @field_validator('itemId')
    @classmethod
    def known_item(cls, value):
        if value not in ITEMS:
            raise ValueError('unknown catalog item')
        return value

    @field_validator('killCauses')
    @classmethod
    def exact_causes(cls, values):
        cleaned = []
        for value in values:
            value = value.strip()
            if not value or len(value) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ValueError('cause must be printable text')
            if value.casefold() in {v.casefold() for v in cleaned}:
                raise ValueError('duplicate cause')
            cleaned.append(value)
        return cleaned


class ItemSettings(ClosedModel):
    enabled: StrictBool = False
    items: list[RestrictedItem] = Field(default_factory=list, max_length=175)
    cooldownSeconds: StrictInt = Field(default=30, ge=5, le=600)

    @model_validator(mode='after')
    def unique_rules(self):
        if len({item.itemId for item in self.items}) != len(self.items):
            raise ValueError('duplicate item')
        causes = [cause.casefold() for item in self.items for cause in item.killCauses]
        if len(causes) != len(set(causes)):
            raise ValueError('a cause must identify only one restricted item')
        if self.enabled and not self.items:
            raise ValueError('select at least one restricted item')
        return self


class ItemUsedEvent(ClosedModel):
    """Optional producer extension, not a field supplied by stock HTTP RCON."""
    type: Literal['itemUsed']
    eventId: str = Field(min_length=1, max_length=128)
    matchId: str = Field(min_length=1, max_length=128)
    mapName: str = Field(min_length=1, max_length=256)
    steamId: str = Field(pattern=r'^[1-9][0-9]{16}$')
    itemId: str = Field(min_length=1, max_length=128)
    itemKind: Literal['weapons', 'equipment']
    occurredAt: datetime

    @field_validator('eventId', 'matchId', 'mapName', 'itemId')
    @classmethod
    def printable(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('invalid event text')
        return value

    @field_validator('occurredAt', mode='before')
    @classmethod
    def timestamp_text(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError('occurredAt must be a timestamp with an explicit time zone')
        return value

    @field_validator('occurredAt')
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('occurredAt requires an explicit time zone')
        return value

    @model_validator(mode='after')
    def catalog_kind(self):
        if self.itemId not in ITEMS or ITEMS[self.itemId]['kind'] != self.itemKind:
            raise ValueError('itemId and itemKind must match the catalog')
        return self

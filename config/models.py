from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class LocationConfig(BaseModel):
    latitude: float = 0.0
    longitude: float = 0.0
    timezone: str = "UTC"
    elevation: float = 0.0


class CalculationConfig(BaseModel):
    method: str = "ISNA"
    asr_madhab: Literal["Standard", "Hanafi"] = "Standard"


class IqamahRuleConfig(BaseModel):
    type: Literal["offset_minutes", "round_up_to", "fixed_time"]
    # offset_minutes
    value: Optional[int] = None
    # round_up_to
    every_n_minutes: Optional[int] = None
    # fixed_time
    hour: Optional[int] = None
    minute: Optional[int] = None

    @model_validator(mode="after")
    def check_required_fields(self) -> "IqamahRuleConfig":
        if self.type == "offset_minutes" and self.value is None:
            raise ValueError("offset_minutes rule requires 'value'")
        if self.type == "round_up_to" and self.every_n_minutes is None:
            raise ValueError("round_up_to rule requires 'every_n_minutes'")
        if self.type == "fixed_time" and (self.hour is None or self.minute is None):
            raise ValueError("fixed_time rule requires 'hour' and 'minute'")
        return self


class IqamahConfig(BaseModel):
    fajr: list[IqamahRuleConfig] = Field(default_factory=list)
    dhuhr: list[IqamahRuleConfig] = Field(default_factory=list)
    asr: list[IqamahRuleConfig] = Field(default_factory=list)
    maghrib: list[IqamahRuleConfig] = Field(default_factory=list)
    isha: list[IqamahRuleConfig] = Field(default_factory=list)


class DisplayConfig(BaseModel):
    theme: str = "dark"
    font_scale: float = 1.0
    clock_format: Literal[12, 24] = 24
    show_seconds: bool = True
    language: str = "en"
    layout: str = "standard"  # standard | minimal | transposed | split | cards


class HijriConfig(BaseModel):
    enabled: bool = True
    adjustment: int = Field(default=0, ge=-3, le=3)


class JumuahConfig(BaseModel):
    enabled: bool = True
    hour: int = Field(default=13, ge=0, le=23)    # 1 PM default
    minute: int = Field(default=15, ge=0, le=59)  # :15 default


class WebConfig(BaseModel):
    port: int = Field(default=8080, ge=1024, le=65535)
    host: str = "0.0.0.0"


class AppConfig(BaseModel):
    location: LocationConfig = Field(default_factory=LocationConfig)
    calculation: CalculationConfig = Field(default_factory=CalculationConfig)
    iqamah_rules: IqamahConfig = Field(default_factory=IqamahConfig)
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    hijri: HijriConfig = Field(default_factory=HijriConfig)
    jumuah: JumuahConfig = Field(default_factory=JumuahConfig)
    web: WebConfig = Field(default_factory=WebConfig)

"""mcp-cloudwatch settings (`MCP_CLOUDWATCH_*`)."""

from __future__ import annotations

from typing import ClassVar

from mcp_common.config import SourceSettingsBase
from pydantic import SecretStr, model_validator
from pydantic_settings import SettingsConfigDict

__all__ = ["DEFAULT_SIMULATE_ACTIONS", "Settings"]

# Write actions that `iam:SimulatePrincipalPolicy` must report as not allowed (ADR-0008 A3).
DEFAULT_SIMULATE_ACTIONS = (
    "logs:PutLogEvents,logs:DeleteLogGroup,logs:CreateLogGroup,logs:PutRetentionPolicy,"
    "cloudwatch:PutMetricData,cloudwatch:DeleteAlarms,cloudwatch:PutMetricAlarm,"
    "cloudwatch:SetAlarmState"
)


class Settings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_CLOUDWATCH_", case_sensitive=False, extra="ignore"
    )

    region: str
    # Static keys are optional: when both are unset boto3's default credential chain is used.
    aws_access_key_id: SecretStr | None = None
    aws_secret_access_key: SecretStr | None = None
    # Optional: assume a read-only role instead of using the base credentials directly.
    aws_role_arn: str | None = None
    # For local emulators / VPC endpoints only.
    endpoint_url: str | None = None
    simulate_actions: str = DEFAULT_SIMULATE_ACTIONS
    # Seconds between `GetQueryResults` polls of a Logs Insights query.
    insights_poll_interval: float = 1.0

    @model_validator(mode="after")
    def _keys_go_together(self) -> Settings:
        if (self.aws_access_key_id is None) != (self.aws_secret_access_key is None):
            raise ValueError(
                "MCP_CLOUDWATCH_AWS_ACCESS_KEY_ID and MCP_CLOUDWATCH_AWS_SECRET_ACCESS_KEY "
                "must be set together"
            )
        return self

    @property
    def simulate_action_list(self) -> list[str]:
        return [a.strip() for a in self.simulate_actions.split(",") if a.strip()]

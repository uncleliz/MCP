"""mcp-sqs-sns settings (`MCP_SQS_SNS_*`)."""

from __future__ import annotations

from typing import ClassVar

from mcp_common.config import SourceSettingsBase
from pydantic import SecretStr, model_validator
from pydantic_settings import SettingsConfigDict

__all__ = ["DEFAULT_SIMULATE_ACTIONS", "WARN_ACTIONS", "Settings"]

# Write actions `iam:SimulatePrincipalPolicy` must report as not allowed (ADR-0003 A1/ADR-0008 A3).
DEFAULT_SIMULATE_ACTIONS = (
    "sqs:SendMessage,sqs:SendMessageBatch,sqs:DeleteMessage,sqs:DeleteMessageBatch,"
    "sqs:PurgeQueue,sqs:CreateQueue,sqs:DeleteQueue,sqs:SetQueueAttributes,"
    "sqs:ChangeMessageVisibility,sqs:TagQueue,sqs:AddPermission,"
    "sns:Publish,sns:CreateTopic,sns:DeleteTopic,sns:SetTopicAttributes,sns:Subscribe,"
    "sns:Unsubscribe,sns:AddPermission"
)
# Allowed by AWS's managed read-only SQS policy but never called by this server: ReceiveMessage
# hides messages from consumers for the visibility timeout, so a policy that grants it is a
# warning (use a least-privilege custom policy), not a refusal to serve.
WARN_ACTIONS = ("sqs:ReceiveMessage",)


class Settings(SourceSettingsBase):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MCP_SQS_SNS_", case_sensitive=False, extra="ignore"
    )

    region: str
    # Static keys are optional: when both are unset boto3's default credential chain is used.
    aws_access_key_id: SecretStr | None = None
    aws_secret_access_key: SecretStr | None = None
    # For LocalStack / VPC endpoints only.
    endpoint_url: str | None = None
    simulate_actions: str = DEFAULT_SIMULATE_ACTIONS

    @model_validator(mode="after")
    def _keys_go_together(self) -> Settings:
        if (self.aws_access_key_id is None) != (self.aws_secret_access_key is None):
            raise ValueError(
                "MCP_SQS_SNS_AWS_ACCESS_KEY_ID and MCP_SQS_SNS_AWS_SECRET_ACCESS_KEY "
                "must be set together"
            )
        return self

    @property
    def simulate_action_list(self) -> list[str]:
        return [a.strip() for a in self.simulate_actions.split(",") if a.strip()]

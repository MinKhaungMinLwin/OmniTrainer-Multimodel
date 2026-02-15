from typing import Literal
from pydantic import BaseModel, Field


class ModerationResult(BaseModel):
    """Base model for moderation results with common fields."""
    rationale: str = Field(description="Explanation of what was harmful and why")
    contains_pii: bool = Field(
        default=False,
        description="Whether the content contains any personally-identifiable information (PII)"
    )
    is_unfriendly: bool = Field(
        default=False,
        description="Whether unfriendly tone or content was detected"
    )
    is_unprofessional: bool = Field(
        default=False,
        description="Whether unprofessional tone or content was detected"
    )


class TextModerationResult(ModerationResult):
    """Text moderation result."""
    # Inherits fields from ModerationResult.
    pass


class ImageModerationResult(ModerationResult):
    """Image moderation result."""
    is_disturbing: bool = Field(default=False, description="Whether the image is disturbing")
    is_low_quality: bool = Field(default=False, description="Whether the image is low quality")


class VideoModerationResult(ModerationResult):
    """Video moderation result."""
    is_disturbing: bool = Field(default=False, description="Whether the video is disturbing")
    is_low_quality: bool = Field(default=False, description="Whether the video is low quality")


class AudioModerationResult(ModerationResult):
    """Audio moderation result."""
    transcription: str = Field(description="The transcription of the audio")
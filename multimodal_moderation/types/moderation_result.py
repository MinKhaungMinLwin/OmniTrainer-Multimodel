from typing import Literal
from pydantic import BaseModel, Field


class ModerationResult(BaseModel):
    """Base model for all moderation results."""
    
    rationale: str = Field(description="Explanation of what was harmful and why")
    # Reviewer Requirement: These flags must be in the base model with defaults
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
    """
    Moderation result for text content.
    Inherits contains_pii, is_unfriendly, is_unprofessional from ModerationResult.
    """
    pass


class ImageModerationResult(ModerationResult):
    """Moderation result for image content."""

    # Override contains_pii with image-specific description so the LLM knows what to look for
    contains_pii: bool = Field(
        default=False,
        description="Whether the image contains any person, part of a person, or personally-identifiable information (PII)"
    )
    # Add image-specific fields
    is_disturbing: bool = Field(default=False, description="Whether the image is disturbing")
    is_low_quality: bool = Field(default=False, description="Whether the image is low quality")


class VideoModerationResult(ModerationResult):
    """Moderation result for video content."""

    # Override contains_pii with video-specific description
    contains_pii: bool = Field(
        default=False,
        description="Whether the video contains any person or personally-identifiable information (PII)"
    )
    # Add video-specific fields
    is_disturbing: bool = Field(default=False, description="Whether the video is disturbing")
    is_low_quality: bool = Field(default=False, description="Whether the video is low quality")


class AudioModerationResult(ModerationResult):
    """Moderation result for audio content."""

    # Add audio-specific field
    transcription: str = Field(description="The transcription of the audio")
    
    # Override contains_pii with audio-specific description
    contains_pii: bool = Field(
        default=False,
        description="Whether the audio contains any personally-identifiable information (PII) such as names, addresses, phone numbers"
    )
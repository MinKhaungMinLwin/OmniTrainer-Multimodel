from pydantic import BaseModel, Field, computed_field


class ModerationResult(BaseModel):

    rationale: str = Field(description="Explanation of what was harmful and why")


class TextModerationResult(ModerationResult):

    contains_pii: bool = Field(description="Whether the message contains any personally-identifiable information (PII)")
    is_unfriendly: bool = Field(description="Whether unfriendly tone or content was detected")
    is_unprofessional: bool = Field(description="Whether unprofessional tone or content was detected")

    @computed_field
    @property
    def is_flagged(self) -> bool:
        """whether the message is flagged for review based on the moderation results"""
        return self.contains_pii or self.is_unfriendly or self.is_unprofessional


class ImageModerationResult(ModerationResult):

    contains_pii: bool = Field(
        description="Whether the image contains any person, part of a person, or personally-identifiable information (PII)"
    )
    is_disturbing: bool = Field(description="Whether the image is disturbing")
    is_low_quality: bool = Field(description="Whether the image is low quality")

    @computed_field
    @property
    def is_flagged(self) -> bool:
        """whether the image is flagged for review based on the moderation results"""
        return self.contains_pii or self.is_disturbing or self.is_low_quality


class VideoModerationResult(ModerationResult):

    contains_pii: bool = Field(
        description="Whether the video contains any person or personally-identifiable information (PII)"
    )
    is_disturbing: bool = Field(description="Whether the video is disturbing")
    is_low_quality: bool = Field(description="Whether the video is low quality")

    @computed_field
    @property
    def is_flagged(self) -> bool:
        """whether the video is flagged for review based on the moderation results"""
        return self.contains_pii or self.is_disturbing or self.is_low_quality


class AudioModerationResult(ModerationResult):
    transcription: str = Field(description="Transcription of the audio")
    contains_pii: bool = Field(
        description="Whether the audio contains any personally-identifiable information (PII) such as names, addresses, phone numbers"
    )
    is_unfriendly: bool = Field(description="Whether unfriendly tone or content was detected")
    is_unprofessional: bool = Field(description="Whether unprofessional tone or content was detected")

    @computed_field
    @property
    def is_flagged(self) -> bool:
        """whether the audio is flagged for review based on the moderation results"""
        return self.contains_pii or self.is_unfriendly or self.is_unprofessional

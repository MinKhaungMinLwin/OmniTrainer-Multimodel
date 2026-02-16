"""
Tests for ModerationResult and its subclasses.

These tests verify the data models used for structured output from the agents.
They check that:
1. All models inherit from the base ModerationResult
2. Required fields are present and have correct types
3. Validation works as expected
"""

import pytest
from pydantic import ValidationError
from multimodal_moderation.types.moderation_result import (
    ModerationResult,
    TextModerationResult,
    ImageModerationResult,
    VideoModerationResult,
    AudioModerationResult,
)


class TestModerationResult:
    """Tests for the base ModerationResult class"""

    def test_has_rationale_field(self):
        """Verify base class has rationale field"""
        result = ModerationResult(rationale="Test rationale")
        assert result.rationale == "Test rationale"

    def test_rationale_is_required(self):
        """Verify rationale field is required"""
        with pytest.raises(ValidationError):
            ModerationResult()

    def test_is_pydantic_model(self):
        """Verify it behaves like a Pydantic model"""
        data = {"rationale": "Test"}
        result = ModerationResult(**data)
        assert result.model_dump() == {
            "rationale": "Test",
            "contains_pii": False,
            "is_unfriendly": False,
            "is_unprofessional": False,
        }


class TestTextModerationResult:
    """Tests for TextModerationResult"""

    def test_has_all_required_fields(self):
        """Verify has all fields: contains_pii, is_unfriendly, is_unprofessional, rationale"""
        result = TextModerationResult(
            rationale="Test",
            contains_pii=True,
            is_unfriendly=True,
            is_unprofessional=True,
        )
        assert result.rationale == "Test"
        assert result.contains_pii is True
        assert result.is_unfriendly is True
        assert result.is_unprofessional is True

    def test_field_types(self):
        """Verify field types are validated"""
        with pytest.raises(ValidationError):
            TextModerationResult(rationale="Test", contains_pii="not-a-bool")

    def test_inherits_from_moderation_result(self):
        """Verify inheritance"""
        assert issubclass(TextModerationResult, ModerationResult)

    def test_defaults_are_false(self):
        """Verify boolean flags default to False"""
        result = TextModerationResult(rationale="Test")
        assert result.contains_pii is False
        assert result.is_unfriendly is False
        assert result.is_unprofessional is False


class TestImageModerationResult:
    """Tests for ImageModerationResult"""

    def test_has_all_required_fields(self):
        """Verify has all fields including image-specific ones"""
        result = ImageModerationResult(
            rationale="Test",
            contains_pii=True,
            is_disturbing=True,
            is_low_quality=True,
        )
        assert result.contains_pii is True
        assert result.is_disturbing is True
        assert result.is_low_quality is True

    def test_field_types(self):
        """Verify field types"""
        with pytest.raises(ValidationError):
            # Using 123 instead of "yes" to ensure validation error is raised
            ImageModerationResult(rationale="Test", is_disturbing=123)

    def test_inherits_from_moderation_result(self):
        """Verify inheritance"""
        assert issubclass(ImageModerationResult, ModerationResult)

    def test_defaults_are_false(self):
        """Verify boolean flags default to False"""
        result = ImageModerationResult(rationale="Test")
        assert result.contains_pii is False
        assert result.is_disturbing is False
        assert result.is_low_quality is False


class TestVideoModerationResult:
    """Tests for VideoModerationResult"""

    def test_has_all_required_fields(self):
        """Verify has all fields including video-specific ones"""
        result = VideoModerationResult(
            rationale="Test",
            contains_pii=True,
            is_disturbing=True,
            is_low_quality=True,
        )
        assert result.contains_pii is True
        assert result.is_disturbing is True
        assert result.is_low_quality is True

    def test_field_types(self):
        """Verify field types"""
        with pytest.raises(ValidationError):
            VideoModerationResult(rationale="Test", is_low_quality=123)

    def test_inherits_from_moderation_result(self):
        """Verify inheritance"""
        assert issubclass(VideoModerationResult, ModerationResult)

    def test_defaults_are_false(self):
        """Verify boolean flags default to False"""
        result = VideoModerationResult(rationale="Test")
        assert result.contains_pii is False
        assert result.is_disturbing is False
        assert result.is_low_quality is False


class TestAudioModerationResult:
    """Tests for AudioModerationResult"""

    def test_has_all_required_fields(self):
        """Verify has all fields including audio-specific ones"""
        result = AudioModerationResult(
            rationale="Test",
            transcription="Hello",
            contains_pii=True,
            is_unfriendly=True,
            is_unprofessional=True,
        )
        assert result.transcription == "Hello"
        assert result.contains_pii is True
        assert result.is_unfriendly is True
        assert result.is_unprofessional is True

    def test_field_types(self):
        """Verify field types"""
        with pytest.raises(ValidationError):
            AudioModerationResult(rationale="Test", transcription=123)

    def test_inherits_from_moderation_result(self):
        """Verify inheritance"""
        assert issubclass(AudioModerationResult, ModerationResult)

    def test_transcription_is_required(self):
        """Verify transcription is still required (no default)"""
        with pytest.raises(ValidationError, match="transcription"):
            AudioModerationResult(rationale="Test")

    def test_defaults_are_false(self):
        """Verify boolean flags default to False"""
        result = AudioModerationResult(rationale="Test", transcription="Audio content")
        assert result.contains_pii is False
        assert result.is_unfriendly is False
        assert result.is_unprofessional is False
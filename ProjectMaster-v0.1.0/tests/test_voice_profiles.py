from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from project_master.integrations.voice.profiles import (
    ConsentRecord,
    ConsentScope,
    RenderPurpose,
    RightsBasis,
    VoiceProfile,
    VoiceReference,
    VoiceRightsError,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def consent(
    *,
    basis: RightsBasis = RightsBasis.SELF_VOICE,
    scopes: tuple[ConsentScope, ...] = (ConsentScope.VOICE_GENERATION,),
    attested: bool = True,
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
    evidence: tuple[str, ...] = (),
) -> ConsentRecord:
    return ConsentRecord(
        id="consent-1",
        basis=basis,
        scopes=scopes,
        subject_label="Profile owner",
        attested_by_user=attested,
        granted_at=NOW - timedelta(days=1),
        expires_at=expires_at,
        revoked_at=revoked_at,
        evidence_artifact_ids=evidence,
    )


def reference() -> VoiceReference:
    return VoiceReference(
        artifact_id="reference-audio-1",
        sha256="a" * 64,
        media_type="audio/wav",
        duration_seconds=12,
        sample_rate_hz=48_000,
        channels=1,
        transcript="Reference transcript.",
    )


def test_reference_profile_enforces_active_attested_rights_and_scope() -> None:
    profile = VoiceProfile.create(
        profile_id="mike",
        name="Mike",
        mode="reference",
        language="en-US",
        consent=consent(
            scopes=(
                ConsentScope.VOICE_GENERATION,
                ConsentScope.PUBLICATION,
            )
        ),
        references=[reference()],
        created_at=NOW,
    )

    profile.assert_authorized(RenderPurpose.PRIVATE, at=NOW)
    profile.assert_authorized(RenderPurpose.PUBLICATION, at=NOW)
    with pytest.raises(VoiceRightsError, match="commercial_use"):
        profile.assert_authorized(RenderPurpose.COMMERCIAL, at=NOW)
    assert len(profile.digest) == 64


def test_explicit_or_licensed_voice_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence artifact"):
        consent(basis=RightsBasis.EXPLICIT_CONSENT)

    record = consent(
        basis=RightsBasis.EXPLICIT_CONSENT,
        evidence=("signed-release-1",),
    )
    profile = VoiceProfile.create(
        profile_id="licensed",
        name="Licensed voice",
        mode="reference",
        language="en",
        consent=record,
        references=[reference()],
        created_at=NOW,
    )
    profile.assert_authorized(RenderPurpose.PRIVATE, at=NOW)


def test_synthetic_reference_is_distinct_from_designed_and_real_person_rights() -> None:
    synthetic_reference = consent(basis=RightsBasis.SYNTHETIC_REFERENCE)
    profile = VoiceProfile.create(
        profile_id="synthetic-reference",
        name="Generated reference",
        mode="reference",
        language="en",
        consent=synthetic_reference,
        references=[reference()],
        created_at=NOW,
    )
    profile.assert_authorized(RenderPurpose.PRIVATE, at=NOW)

    with pytest.raises(ValidationError, match="synthetic_design"):
        VoiceProfile.create(
            profile_id="invalid-designed-reference",
            name="Invalid designed reference",
            mode="designed",
            language="en",
            consent=synthetic_reference,
            description="Generated reference cannot masquerade as a designed voice.",
            created_at=NOW,
        )

    with pytest.raises(ValidationError, match="audio-reference rights basis"):
        VoiceProfile.create(
            profile_id="invalid-reference-design",
            name="Invalid reference design",
            mode="reference",
            language="en",
            consent=consent(basis=RightsBasis.SYNTHETIC_DESIGN),
            references=[reference()],
            created_at=NOW,
        )


def test_expired_revoked_or_unattested_rights_are_blocked() -> None:
    for record, message in (
        (consent(attested=False), "not been attested"),
        (consent(expires_at=NOW), "expired"),
        (consent(revoked_at=NOW), "revoked"),
    ):
        profile = VoiceProfile.create(
            profile_id=f"profile-{message.split()[0]}",
            name="Blocked voice",
            mode="reference",
            language="en",
            consent=record,
            references=[reference()],
            created_at=NOW,
        )
        with pytest.raises(VoiceRightsError, match=message):
            profile.assert_authorized(RenderPurpose.PRIVATE, at=NOW)


def test_designed_voice_cannot_smuggle_a_real_person_reference() -> None:
    designed_consent = consent(basis=RightsBasis.SYNTHETIC_DESIGN)
    designed = VoiceProfile.create(
        profile_id="designed",
        name="Designed voice",
        mode="designed",
        language="en",
        consent=designed_consent,
        description="Warm low-register fictional narrator.",
        created_at=NOW,
    )
    designed.assert_authorized(RenderPurpose.PRIVATE, at=NOW)

    with pytest.raises(ValidationError, match="cannot include"):
        VoiceProfile.create(
            profile_id="invalid",
            name="Invalid",
            mode="designed",
            language="en",
            consent=designed_consent,
            references=[reference()],
            description="Invalid mixture.",
            created_at=NOW,
        )

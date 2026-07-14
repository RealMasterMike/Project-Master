from project_master.core.audit import audit_response


def test_flags_unframed_ai_emotion() -> None:
    findings = audit_response("I am excited to build this.")
    assert any(item.code == "ai-emotion-framing" for item in findings)


def test_clean_text_can_pass() -> None:
    findings = audit_response(
        "The available evidence suggests this conclusion, but confidence is moderate."
    )
    assert findings == []

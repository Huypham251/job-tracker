from app.core.privacy import message_ref


def test_message_ref_is_stable_short_and_does_not_contain_the_id() -> None:
    ref = message_ref("1a0d5527a5984162")
    assert ref == message_ref("1a0d5527a5984162")
    assert ref.startswith("msg-") and len(ref) == 14
    assert "1a0d5527a5984162" not in ref
    assert ref != message_ref("1a0d54ed644451fb")

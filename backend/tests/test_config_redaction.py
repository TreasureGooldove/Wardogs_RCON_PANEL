"""Secret config values stay server-side across editable drafts and validation."""

import pytest

from app.errors import PanelError
from app.rcon.config_redaction import (
    mask_config,
    public_config_document,
    public_config_result,
    restore_config,
)


CURRENT = (
    "[/Script/WDGame.WDRconSettings]\r\n"
    "Password=private-one\r\n"
    "PasswordHash=private-two\r\n"
    "Token=private-three\r\n"
    '+Accounts=(Name="Editor",PasswordHash="private-four")\r\n'
    "; old password was private-five\r\n"
    "[/Script/WDGame.WDGameSession]\r\n"
    "ServerName=Old Name\r\n"
    "ServerPassword=private-six\r\n"
)


def test_mask_restore_preserves_exact_original_secrets_and_nonsecret_edits():
    masked = mask_config(CURRENT, "rev-1")
    assert "private-" not in masked
    assert masked.count("__WD_REDACTED_rev-1__") == 6
    changed = masked.replace("ServerName=Old Name", "ServerName=New Name")
    restored = restore_config(changed, CURRENT, "rev-1")
    assert restored == CURRENT.replace("ServerName=Old Name", "ServerName=New Name")
    assert "\n" not in restored.replace("\r\n", "")


def test_empty_join_password_stays_empty_in_server_draft():
    current = "[/Script/WDGame.WDGameSession]\nServerPassword=\n"
    masked = mask_config(current, "rev-1")
    assert masked == current
    assert restore_config(masked, current, "rev-1") == current


def test_bom_and_secret_comments_are_masked():
    current = "\ufeffPassword=bom-secret\r\n; Token=comment-secret\r\n"
    masked = mask_config(current, "rev-1")
    assert "bom-secret" not in masked and "comment-secret" not in masked
    assert restore_config(masked, current, "rev-1") == current


def test_secret_rotation_or_explicit_clear_is_possible_without_old_value():
    masked = mask_config(CURRENT, "rev-1")
    changed = masked.replace("Password=__WD_REDACTED_rev-1__", "Password=rotated-value", 1)
    changed = changed.replace("ServerPassword=__WD_REDACTED_rev-1__", "ServerPassword=")
    restored = restore_config(changed, CURRENT, "rev-1")
    assert "Password=rotated-value" in restored
    assert "ServerPassword=\r\n" in restored
    assert "PasswordHash=private-two" in restored


@pytest.mark.parametrize("modify", [
    lambda text: text.replace("__WD_REDACTED_rev-1__", "__WD_REDACTED_rev-2__", 1),
    lambda text: text.replace("Password=__WD_REDACTED_rev-1__\r\n", ""),
    lambda text: text.replace("Password=__WD_REDACTED_rev-1__", "Other=__WD_REDACTED_rev-1__"),
    lambda text: text.replace("ServerPassword=__WD_REDACTED_rev-1__", "ServerPassword=foo__WD_REDACTED_rev-1__"),
])
def test_wrong_revision_missing_or_moved_placeholder_fails_closed(modify):
    with pytest.raises(PanelError) as exc:
        restore_config(modify(mask_config(CURRENT, "rev-1")), CURRENT, "rev-1")
    assert exc.value.code == "invalid_config"


def test_public_config_response_never_proxies_unknown_upstream_text():
    public = public_config_document({
        "revision": "rev-1", "writable": True, "text": CURRENT,
        "sections": [{"secret": "private-seven"}],
        "warnings": ["Password=private-eight"],
    })
    assert public["sections"] == []
    assert public["warnings"] == ["服务器返回配置警告，详细内容已隐藏"]
    assert "private-" not in str(public)
    result = public_config_result({
        "ok": False, "revision": "rev-2",
        "errors": [{"section": "/Script/WDGame.WDRconSettings", "key": "Password",
                    "code": "private-nine", "message": "private-ten"}],
        "warnings": ["private-eleven"], "outcomes": ["private-twelve"],
        "changed": ["private-thirteen"],
    }, public["text"])
    assert result["errors"][0]["key"] == "Password"
    assert result["errors"][0]["code"] == "validation_error"
    assert "private-" not in str(result)

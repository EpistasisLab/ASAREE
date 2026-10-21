"""Plain Markdown personas need no frontmatter or separate metadata form."""

import pytest

from asaree.services.personas import MAX_PROMPT_BYTES, decode_persona_upload, persona_name, prompt_sha256


def test_plain_markdown_upload_keeps_exact_text_and_suggests_filename_name() -> None:
    prompt = "You are methodical.\n\nCheck your assumptions.\n"
    filename, text = decode_persona_upload(r"C:\personas\careful_analyst.md", prompt.encode())
    assert filename == "careful_analyst.md"
    assert text == prompt
    assert persona_name(None, filename, text) == "careful analyst"
    assert persona_name("My persona", filename, text) == "My persona"


def test_direct_text_can_be_named_automatically_and_empty_baseline_is_valid() -> None:
    assert persona_name(None, None, "\n# Careful analyst\nInstructions") == "Careful analyst"
    assert persona_name(None, None, "") == "Persona"
    assert persona_name(None, None, "#") == "Persona"
    assert decode_persona_upload("baseline.md", b"") == ("baseline.md", "")
    assert prompt_sha256("same text") == prompt_sha256("same text")
    assert prompt_sha256("same text") != prompt_sha256("changed text")


@pytest.mark.parametrize("filename, raw, message", [
    ("persona.txt", b"text", "Markdown"),
    ("persona.md", b"\xff", "UTF-8"),
    ("persona.md", b"x" * (MAX_PROMPT_BYTES + 1), "1 MB"),
])
def test_invalid_upload_rejected(filename: str, raw: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        decode_persona_upload(filename, raw)

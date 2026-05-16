"""Golden-fixture parser tests against real TallyPrime response bodies.

Captured from a live TallyPrime instance on 2026-05-16 and stored under
`tests/fixtures/tally_responses/`. These are the tests P0.46b was missing:
unit tests with hand-crafted synthetic XML let `_parse_ledgers_list` ship
with two latent bugs (NAME read as child element instead of attribute;
GSTIN read from REGISTRATIONTYPE which is actually the registration-type
enum) because the fixtures matched the wrong-shape parser.

These fixtures are the contract: if a future Tally release changes the
shape, this test trips and we update both the parser and the fixture
together.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from connector.tally_client import TallyClient

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tally_responses"


@pytest.fixture
def client() -> TallyClient:
    return TallyClient(host="localhost", port=9000, timeout=5.0)


def _load(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_get_all_ledgers_against_real_response(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_load("ledgers_collection.xml"),
    )
    ledgers = await client.get_all_ledgers()

    # Captured Tally instance has these five user-visible ledgers; if Tally
    # ever changes shape, len > 0 alone doesn't fail loudly enough.
    names = {l.name for l in ledgers}
    assert names == {
        "ABC LTD",
        "Cash",
        "HDFC BANK",
        "Profit & Loss A/c",
        "Xyz Ltd",
    }

    by_name = {l.name: l for l in ledgers}
    assert by_name["ABC LTD"].parent_group == "Sundry Creditors"
    assert by_name["HDFC BANK"].parent_group == "Bank Accounts"
    # `Profit & Loss A/c` is parented to the system-reserved "Primary"
    # group, which Tally emits as `\x04 Primary`. The parser must strip
    # the control prefix so persistence stores a clean string.
    assert by_name["Profit & Loss A/c"].parent_group == "Primary"

    # The captured test company has no GST-registered party ledgers, so
    # every gstin is None. The synthetic test below covers the populated
    # case.
    assert all(l.gstin is None for l in ledgers)


@pytest.mark.asyncio
async def test_get_all_groups_against_real_response(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url="http://localhost:9000",
        status_code=200,
        text=_load("groups_collection.xml"),
    )
    groups = await client.get_all_groups()

    # 28 user-visible groups in the captured response. The exact count is
    # the standard TallyPrime default chart-of-accounts; if Tally ships a
    # new default group, this fails loudly and we update the fixture.
    assert len(groups) == 28

    by_name = {g.name: g for g in groups}
    assert by_name["Bank Accounts"].parent == "Current Assets"
    # System-reserved parent must come through stripped.
    assert by_name["Capital Account"].parent == "Primary"
    assert by_name["Branch / Divisions"].parent == "Primary"


@pytest.mark.asyncio
async def test_partygstin_extraction_when_present(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    # The live test company has no GST-registered ledgers, so this case
    # cannot be covered by a real fixture. Synthetic XML in the exact
    # shape Tally emits (NAME attribute, child PARENT, child PARTYGSTIN)
    # exercises the parser when PartyGSTIN IS present.
    xml = (
        '<ENVELOPE><BODY><DATA>'
        '<COLLECTION ISMSTDEPTYPE="Yes" MSTDEPTYPE="8">'
        '<LEDGER NAME="GST Registered Party" RESERVEDNAME="">'
        '<PARENT TYPE="String">Sundry Debtors</PARENT>'
        '<PARTYGSTIN>29ABCDE1234F1Z5</PARTYGSTIN>'
        '</LEDGER>'
        '</COLLECTION></DATA></BODY></ENVELOPE>'
    )
    httpx_mock.add_response(
        url="http://localhost:9000", status_code=200, text=xml
    )
    ledgers = await client.get_all_ledgers()
    assert len(ledgers) == 1
    assert ledgers[0].gstin == "29ABCDE1234F1Z5"


@pytest.mark.asyncio
async def test_sanitizer_strips_invalid_xml_char_refs(
    client: TallyClient, httpx_mock: HTTPXMock
) -> None:
    # Tally inlines `&#4;` (EOT) as a reserved-master marker. XML 1.0
    # forbids that codepoint in text, so expat would raise ParseError if
    # the response weren't sanitized at the boundary. This test asserts
    # the sanitization path is wired through `_post_xml`.
    xml = (
        '<ENVELOPE><BODY><DATA>'
        '<COLLECTION ISMSTDEPTYPE="Yes" MSTDEPTYPE="4">'
        '<GROUP NAME="Capital Account" RESERVEDNAME="Capital Account">'
        '<PARENT TYPE="String">&#4; Primary</PARENT>'
        '</GROUP>'
        '</COLLECTION></DATA></BODY></ENVELOPE>'
    )
    httpx_mock.add_response(
        url="http://localhost:9000", status_code=200, text=xml
    )
    groups = await client.get_all_groups()
    assert len(groups) == 1
    assert groups[0].name == "Capital Account"
    assert groups[0].parent == "Primary"

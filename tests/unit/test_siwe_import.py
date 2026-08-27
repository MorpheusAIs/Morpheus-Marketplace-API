"""siwe must import; abnf>=2.9 redefines ALPHA and crashes process boot."""
import pytest

siwe = pytest.importorskip("siwe")


def test_siwe_message_import():
    from siwe import SiweMessage

    assert SiweMessage is not None

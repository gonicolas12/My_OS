"""Tests de models.secrets avec un trousseau (keyring) mocké.

Aucun trousseau réel n'est touché : on injecte un faux module keyring via
``monkeypatch`` sur :func:`models.secrets._keyring`. On vérifie surtout les
garanties de sécurité : la clé ne fuit pas, un échec de trousseau est traité
comme « pas de clé », une clé vide est refusée.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument
from __future__ import annotations

from types import SimpleNamespace

import pytest

from models import secrets


class _FakeKeyring:
    """Faux trousseau en mémoire, fidèle à l'API keyring (service, nom, valeur)."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, name: str, value: str) -> None:
        self.store[(service, name)] = value

    def get_password(self, service: str, name: str) -> str | None:
        return self.store.get((service, name))

    def delete_password(self, service: str, name: str) -> None:
        del self.store[(service, name)]  # KeyError si absent, comme keyring


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets, "_keyring", lambda: fake)
    return fake


def test_pas_de_cle_au_depart(fake_keyring: _FakeKeyring) -> None:
    assert secrets.has_api_key() is False
    assert secrets.get_api_key() is None


def test_set_puis_get(fake_keyring: _FakeKeyring) -> None:
    secrets.set_api_key("sk-ant-secret")
    assert secrets.get_api_key() == "sk-ant-secret"
    assert secrets.has_api_key() is True


def test_cle_stockee_sous_le_bon_service_et_nom(fake_keyring: _FakeKeyring) -> None:
    secrets.set_api_key("sk-ant-secret")
    assert fake_keyring.store == {
        (secrets.SERVICE_NAME, secrets.API_KEY_NAME): "sk-ant-secret"
    }


def test_set_rogne_les_espaces(fake_keyring: _FakeKeyring) -> None:
    secrets.set_api_key("  sk-ant-trim  \n")
    assert secrets.get_api_key() == "sk-ant-trim"


def test_set_cle_vide_leve_value_error(fake_keyring: _FakeKeyring) -> None:
    with pytest.raises(ValueError):
        secrets.set_api_key("")
    with pytest.raises(ValueError):
        secrets.set_api_key("   ")
    assert secrets.has_api_key() is False


def test_delete_est_idempotent(fake_keyring: _FakeKeyring) -> None:
    secrets.set_api_key("sk-ant-secret")
    secrets.delete_api_key()
    assert secrets.has_api_key() is False
    # Un second delete ne lève pas, même si l'entrée est déjà absente.
    secrets.delete_api_key()


def test_erreur_de_trousseau_traitee_comme_pas_de_cle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Backend keyring indisponible/verrouillé : get_password lève. On doit
    # renvoyer None (repli local), jamais propager l'exception.
    def _boom(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("aucun backend keyring")

    broken = SimpleNamespace(get_password=_boom)
    monkeypatch.setattr(secrets, "_keyring", lambda: broken)

    assert secrets.get_api_key() is None
    assert secrets.has_api_key() is False

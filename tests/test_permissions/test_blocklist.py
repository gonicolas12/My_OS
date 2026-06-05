"""Tests de la blocklist absolue (niveau 3)."""

# pylint: disable=missing-function-docstring
from __future__ import annotations

import pytest

from permissions.blocklist import is_blocked


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/etc",
        "/boot",
        "/usr",
        "/var/lib",
        "/sys",
        "/proc",
        "/root",
        "/opt",
        "/home",
    ],
)
def test_delete_file_de_la_racine_systeme_est_bloque(path: str) -> None:
    assert is_blocked("delete_file", {"path": path}) is True


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "/boot/grub/themes/x.png",
        "/usr/local/bin/foo",
        "/home/alice/document.txt",
        "/tmp/y",
        "/var/log/syslog",
    ],
)
def test_delete_file_d_un_fichier_dedans_n_est_pas_bloque(path: str) -> None:
    # Niveau 2 (confirmation) géré ailleurs ; la blocklist ne couvre
    # que la racine, jamais les fichiers *à l'intérieur*.
    assert is_blocked("delete_file", {"path": path}) is False


def test_move_file_dont_la_source_est_racine_est_bloque() -> None:
    assert is_blocked("move_file", {"src": "/etc", "dst": "/tmp/etc_backup"}) is True


def test_move_file_dont_la_destination_est_racine_est_bloque() -> None:
    assert is_blocked("move_file", {"src": "/tmp/x", "dst": "/etc"}) is True


def test_move_file_entre_fichiers_quelconques_n_est_pas_bloque() -> None:
    assert is_blocked("move_file", {"src": "/tmp/a", "dst": "/tmp/b"}) is False


@pytest.mark.parametrize(
    "path",
    [
        "/boot/grub/grub.cfg",
        "/boot/vmlinuz-linux",
        "/boot/initramfs-linux.img",
        "/boot/efi/EFI/grub/grubx64.efi",
    ],
)
def test_write_file_dans_bootloader_est_bloque(path: str) -> None:
    assert is_blocked("write_file", {"path": path}) is True
    assert is_blocked("create_file", {"path": path}) is True


def test_write_file_quelconque_n_est_pas_bloque() -> None:
    assert is_blocked("write_file", {"path": "/home/alice/note.md"}) is False
    assert (
        is_blocked("write_file", {"path": "/etc/myconf"}) is False
    )  # niveau 2, pas bloqué


def test_outil_non_liste_n_est_jamais_bloque() -> None:
    # Les outils sans entrée dans _BLOCKERS passent toujours.
    assert is_blocked("read_file", {"path": "/etc/passwd"}) is False
    assert is_blocked("list_dir", {"path": "/"}) is False
    assert is_blocked("hypothetical_future_tool", {"foo": "bar"}) is False


def test_traversee_de_repertoire_est_normalisee() -> None:
    # "/etc/../" se résout en "/" → bloqué.
    assert is_blocked("delete_file", {"path": "/etc/.."}) is True
    # "/tmp/../etc" → "/etc" → bloqué.
    assert is_blocked("delete_file", {"path": "/tmp/../etc"}) is True


def test_args_sans_path_n_est_pas_bloque() -> None:
    # Une absence de champ ne lève pas, ne bloque pas.
    assert is_blocked("delete_file", {}) is False
    assert is_blocked("move_file", {}) is False
    assert is_blocked("write_file", {"content": "x"}) is False


def test_path_non_string_n_est_pas_bloque() -> None:
    # Si le LLM envoie un type bizarre, on refuse de paniquer.
    assert is_blocked("delete_file", {"path": 42}) is False
    assert is_blocked("delete_file", {"path": None}) is False

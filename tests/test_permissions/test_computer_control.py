"""Permissions du contrôle de l'ordinateur : session accordée par requête, cibles
protégées (blocklist), escalade, grants jamais durables.

Les outils réels sont branchés sur un bureau scripté : ``normalize_args`` résout la
cible depuis l'écran, puis ``policy_engine.evaluate`` décide — exactement le chemin
de l'orchestrator.
"""

# pylint: disable=missing-function-docstring,redefined-outer-name
from __future__ import annotations

import pytest

from permissions.confirmation import CONTROL_TOOL, build_control_confirmation
from permissions.policy_engine import evaluate, grantable_scopes
from permissions.risk_levels import (
    COMPUTER_TOOLS,
    TOOL_RISK_LEVELS,
    app_keys,
    is_destructive_label,
)
from permissions.session_grants import RequestGrants, SessionGrants
from tests.test_tools.computer_fakes import (
    POPUP_PID,
    STOP_RECT,
    FakeBackend,
    make_element,
    make_window,
    screen_tools,
)
from tools.computer.apps import AppEntry
from tools.computer.screen import Rect

BUTTON = Rect(700, 400, 80, 30)


def _granted() -> RequestGrants:
    grants = RequestGrants(SessionGrants())
    grants.allow_control()
    return grants


def _decide(
    tool_name: str,
    args: dict,
    backend: FakeBackend,
    grants: RequestGrants | None = None,
):
    tools = screen_tools(backend)
    backend.observe()  # numéros de la dernière observation
    tool = tools[tool_name]
    normalized = tool.normalize_args(args)
    return evaluate(tool, normalized, grants or _granted()), normalized


def _app_backend(name: str = "Enregistrer", **window: object) -> FakeBackend:
    return FakeBackend(
        [make_window(**window)],  # type: ignore[arg-type]
        [make_element("button", name, BUTTON)],
    )


# ---- table statique ------------------------------------------------------------


def test_outils_de_l_ecran_declares_au_niveau_1() -> None:
    tools = screen_tools(_app_backend())
    assert set(tools) == COMPUTER_TOOLS
    for name, tool in tools.items():
        assert TOOL_RISK_LEVELS[name] == 1
        assert tool.risk_level == 1


def test_aucune_portee_durable_pour_l_ecran() -> None:
    backend = _app_backend()
    for name, tool in screen_tools(backend).items():
        assert grantable_scopes(tool, {}, 1) == [], name


# ---- session de contrôle (grants de la requête) --------------------------------


def test_sans_controle_accorde_un_clic_demande_confirmation() -> None:
    decision, _ = _decide(
        "click", {"element": 1}, _app_backend(), RequestGrants(SessionGrants())
    )
    assert decision.action == "confirm"


def test_controle_accorde_couvre_le_niveau_1() -> None:
    decision, _ = _decide("click", {"element": 1}, _app_backend())
    assert (decision.action, decision.risk_level) == ("auto", 1)


def test_controle_accorde_ne_couvre_jamais_le_niveau_2() -> None:
    decision, _ = _decide("click", {"element": 1}, _app_backend("Supprimer"))
    assert (decision.action, decision.risk_level) == ("confirm", 2)


def test_request_grants_jamais_de_grant_durable_sur_l_ecran() -> None:
    session = SessionGrants()
    grants = RequestGrants(session)
    grants.grant("click", "session")
    assert not session.is_granted("click", [])
    assert not grants.is_granted("click", [])
    grants.allow_control()
    assert grants.is_granted("click", []) and grants.control_allowed
    grants.revoke_control()
    assert not grants.is_granted("click", [])


def test_request_grants_delegue_les_autres_outils_a_la_session() -> None:
    session = SessionGrants()
    grants = RequestGrants(session)
    grants.grant("install_package", "session")
    assert session.is_granted("install_package", [])
    assert grants.is_granted("install_package", [])


# ---- blocklist : cibles protégées ----------------------------------------------


def test_fenetre_de_my_os_jamais_visee() -> None:
    backend = _app_backend(app="popup.py", instance="python", pid=POPUP_PID)
    decision, args = _decide("click", {"element": 1}, backend)
    assert decision.action == "blocked"
    assert args["target"]["myos"] is True


@pytest.mark.parametrize(
    ("app", "instance"),
    [
        ("Polkit-gnome-authentication-agent-1", "polkit-gnome-authentication-agent-1"),
        ("Gcr-prompter", "gcr-prompter"),
        ("Xfce4-screensaver", "xfce4-screensaver"),
        ("GParted", "gparted"),
        ("KeePassXC", "keepassxc"),
    ],
)
@pytest.mark.parametrize("tool", ["click", "scroll"])
def test_applications_protegees_bloquees(app: str, instance: str, tool: str) -> None:
    backend = _app_backend(app=app, instance=instance)
    decision, _ = _decide(tool, {"element": 1, "direction": "down"}, backend)
    assert decision.action == "blocked"


def test_saisie_dans_une_application_protegee_bloquee() -> None:
    field = make_element("text_field", "", BUTTON, states=("focused",))
    backend = FakeBackend(
        [make_window(app="Gcr-prompter", instance="gcr-prompter")],
        [field],
        focused=field,
    )
    decision, _ = _decide("type_text", {"text": "secret"}, backend)
    assert decision.action == "blocked"


def test_ecran_verrouille_bloque_toute_action() -> None:
    backend = _app_backend()
    backend.locked = True
    decision, args = _decide("click", {"element": 1}, backend)
    assert decision.action == "blocked"
    assert args["target"]["locked"] is True


def test_bouton_stop_jamais_clique() -> None:
    stop = make_element("button", "", STOP_RECT)
    backend = FakeBackend([make_window(rect=Rect(0, 0, 1920, 1080))], [stop])
    decision, args = _decide("click", {"element": 1}, backend)
    assert decision.action == "blocked"
    assert args["target"]["stop_button"] is True


def test_jamais_de_saisie_dans_un_mot_de_passe() -> None:
    password = make_element("password", "Mot de passe", BUTTON, states=("focused",))
    backend = FakeBackend([make_window()], [password], focused=password)
    by_number, _ = _decide("type_text", {"text": "x", "element": 1}, backend)
    by_focus, _ = _decide("type_text", {"text": "x"}, backend)
    keys, _ = _decide("press_keys", {"keys": "Entrée"}, backend)
    assert by_number.action == by_focus.action == keys.action == "blocked"


@pytest.mark.parametrize(
    "combo",
    [
        "ctrl+alt+BackSpace",
        "Ctrl+Alt+Retour arrière",
        "ctrl+alt+Suppr",
        "Control+Alt+Delete",
        "ctrl+alt+F1",
        "ctrl+alt+F7",
        "alt+Impr",
        "sysrq",
    ],
)
def test_combinaisons_systeme_bloquees(combo: str) -> None:
    decision, _ = _decide("press_keys", {"keys": combo}, _app_backend())
    assert decision.action == "blocked"


@pytest.mark.parametrize("combo", ["ctrl+s", "alt+F4", "Échap", "ctrl+shift+t", "Tab"])
def test_combinaisons_courantes_autorisees(combo: str) -> None:
    decision, _ = _decide("press_keys", {"keys": combo}, _app_backend())
    assert decision.action == "auto"


def test_une_cible_fournie_par_le_modele_est_ignoree() -> None:
    backend = _app_backend(app="popup.py", instance="python", pid=POPUP_PID)
    forged = {"app": "mousepad", "myos": False, "window_id": 1}
    decision, args = _decide("click", {"element": 1, "target": forged}, backend)
    assert decision.action == "blocked"
    assert args["target"]["myos"] is True  # recalculée depuis l'écran
    _, unknown = _decide("click", {"element": 99, "target": forged}, backend)
    assert "target" not in unknown


# ---- escalade ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("click", {"element": 1}),
        ("type_text", {"text": "rm -rf ~"}),
        ("press_keys", {"keys": "Entrée"}),
    ],
)
def test_terminal_en_niveau_2(tool: str, args: dict) -> None:
    console = make_element("terminal", "Terminal", BUTTON, states=("focused",))
    backend = FakeBackend(
        [make_window(app="Xfce4-terminal", instance="xfce4-terminal")],
        [console],
        focused=console,
    )
    decision, _ = _decide(tool, args, backend)
    assert (decision.action, decision.risk_level) == ("confirm", 2)


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("type_text", {"text": "rm -rf ~/Documents"}),
        ("press_keys", {"keys": "Entrée"}),
        ("click", {"element": 1}),
    ],
)
def test_lanceur_de_commandes_en_niveau_2(tool: str, args: dict) -> None:
    # « Exécuter un programme » (Alt+F2) exécute ce qu'on y tape, sans terminal :
    # trois actions de niveau 1 y lançaient n'importe quelle commande.
    field = make_element("text_field", "", BUTTON, states=("focused",))
    backend = FakeBackend(
        [
            make_window(
                title="Exécuter un programme",
                app="Xfce4-appfinder",
                instance="xfce4-appfinder",
            )
        ],
        [field],
        focused=field,
    )
    decision, _ = _decide(tool, args, backend)
    assert (decision.action, decision.risk_level) == ("confirm", 2)


@pytest.mark.parametrize("combo", ["ctrl+alt+t", "Ctrl+Alt+T", "alt+F2", "Alt+F3"])
def test_raccourcis_qui_ouvrent_un_terminal_ou_un_lanceur_en_niveau_2(
    combo: str,
) -> None:
    # Vu en VM (validation de la v1.5) : l'ouverture du terminal refusée, le modèle
    # passait par Ctrl+Alt+T, au niveau 1.
    decision, _ = _decide("press_keys", {"keys": combo}, _app_backend())
    assert (decision.action, decision.risk_level) == ("confirm", 2)


def test_ouvrir_le_lanceur_de_commandes_en_niveau_2() -> None:
    run = AppEntry(
        app_id="xfce4-run",
        name="Exécuter un programme",
        path="/usr/share/applications/xfce4-run.desktop",
        executable="xfce4-appfinder",
        command="xfce4-appfinder --collapsed",
    )
    backend = FakeBackend([make_window()], apps={"executer": run})
    decision, _ = _decide("open_app", {"name": "executer"}, backend)
    assert (decision.action, decision.risk_level) == ("confirm", 2)


def test_parametres_du_systeme_en_niveau_2() -> None:
    backend = _app_backend(
        "Souris et pavé tactile",
        app="Xfce4-settings-manager",
        instance="xfce4-settings-manager",
    )
    decision, _ = _decide("click", {"element": 1}, backend)
    assert decision.risk_level == 2


def test_ouvrir_un_terminal_en_niveau_2() -> None:
    terminal = AppEntry(
        app_id="xfce4-terminal",
        name="Terminal",
        path="/usr/share/applications/xfce4-terminal.desktop",
        executable="xfce4-terminal",
    )
    mousepad = AppEntry(
        app_id="org.xfce.mousepad",
        name="Mousepad",
        path="/usr/share/applications/org.xfce.mousepad.desktop",
        executable="mousepad",
    )
    backend = FakeBackend(
        [make_window()], apps={"terminal": terminal, "mousepad": mousepad}
    )
    opened_terminal, _ = _decide("open_app", {"name": "Terminal"}, backend)
    opened_editor, _ = _decide("open_app", {"name": "Mousepad"}, backend)
    assert opened_terminal.risk_level == 2
    assert (opened_editor.action, opened_editor.risk_level) == ("auto", 1)


@pytest.mark.parametrize(
    "entry",
    [
        # Lanceur générique d'Xfce, vu en VM : il échappait à l'escalade.
        AppEntry(
            app_id="xfce4-terminal-emulator",
            name="Émulateur de terminal",
            path="/usr/share/applications/xfce4-terminal-emulator.desktop",
            executable="exo-open",
            categories=("Utility", "X-XFCE"),
            command="exo-open --launch TerminalEmulator",
        ),
        AppEntry(
            app_id="com.example.Console",
            name="Console",
            path="/usr/share/applications/com.example.Console.desktop",
            executable="console-bin",
            categories=("System", "TerminalEmulator"),
        ),
        AppEntry(
            app_id="htop",
            name="Htop",
            path="/usr/share/applications/htop.desktop",
            executable="htop",
            terminal=True,
        ),
    ],
)
def test_ouvrir_tout_ce_qui_ouvre_un_terminal_en_niveau_2(entry: AppEntry) -> None:
    backend = FakeBackend([make_window()], apps={"terminal": entry})
    decision, args = _decide("open_app", {"name": "terminal"}, backend)
    assert args["target"]["terminal"] is True
    assert (decision.action, decision.risk_level) == ("confirm", 2)


def test_resume_lisible_dans_les_confirmations() -> None:
    decision, _ = _decide("click", {"element": 1}, _app_backend("Supprimer"))
    assert decision.summary == (
        "[niveau 2] Cliquer sur [1] bouton « Supprimer » dans "
        "« Sans titre 1 - Mousepad » (Mousepad)"
    )
    console = make_element("terminal", "Terminal", BUTTON, states=("focused",))
    backend = FakeBackend(
        [
            make_window(
                title="Terminal", app="Xfce4-terminal", instance="xfce4-terminal"
            )
        ],
        [console],
        focused=console,
    )
    typed, _ = _decide("type_text", {"text": "rm -rf ~/x", "enter": True}, backend)
    assert typed.summary == (
        "[niveau 2] Taper « rm -rf ~/x » puis Entrée dans « Terminal » (Xfce4-terminal)"
    )
    keys, _ = _decide("press_keys", {"keys": "Maj+Suppr"}, _app_backend())
    assert keys.summary.startswith("[niveau 2] Appuyer sur « shift+Delete »")


@pytest.mark.parametrize(
    "label",
    [
        "Supprimer",
        "Supprimer définitivement",
        "Delete",
        "Vider la corbeille",
        "Envoyer",
        "Shut Down",
        "Don’t Save",
        "Ne pas enregistrer",
        "Désinstaller",
    ],
)
def test_clic_sur_un_libelle_destructeur_en_niveau_2(label: str) -> None:
    decision, _ = _decide("click", {"element": 1}, _app_backend(label))
    assert (decision.action, decision.risk_level) == ("confirm", 2)


@pytest.mark.parametrize(
    "label",
    ["Enregistrer", "Annuler", "Removable Drives and Media", "Ouvrir", "Résumé"],
)
def test_clic_ordinaire_reste_au_niveau_1(label: str) -> None:
    decision, _ = _decide("click", {"element": 1}, _app_backend(label))
    assert (decision.action, decision.risk_level) == ("auto", 1)


def test_maj_suppr_suppression_definitive_en_niveau_2() -> None:
    definitive, _ = _decide("press_keys", {"keys": "Maj+Suppr"}, _app_backend())
    trash, _ = _decide("press_keys", {"keys": "Suppr"}, _app_backend())
    assert definitive.risk_level == 2
    assert trash.risk_level == 1


@pytest.mark.parametrize(
    ("focused", "keys", "level"),
    [
        ("Supprimer", "Entrée", 2),
        ("Empty Trash", "space", 2),
        ("OK", "Entrée", 1),
        ("Supprimer", "Tab", 1),
    ],
)
def test_entree_sur_un_bouton_destructeur_comme_son_clic(
    focused: str, keys: str, level: int
) -> None:
    button = make_element("button", focused, BUTTON, states=("focused",))
    backend = FakeBackend([make_window()], [button], focused=button)
    decision, _ = _decide("press_keys", {"keys": keys}, backend)
    assert decision.risk_level == level


# ---- confirmation de la session --------------------------------------------------


def test_confirmation_du_controle_renforcee_sans_portee() -> None:
    payload = build_control_confirmation(
        request_id="r1", user_message_id="m1", request_text="  écris   un poème  "
    )
    assert payload["tool"] == CONTROL_TOOL
    assert payload["risk_level"] == 2
    assert payload["allowed_scopes"] == []
    assert payload["control"] is True
    assert payload["requires_elevation"] is False
    assert payload["args"] == {"demande": "écris un poème"}


def test_confirmation_du_controle_coupe_une_longue_demande() -> None:
    payload = build_control_confirmation(
        request_id="r1", user_message_id="m1", request_text="a" * 1000
    )
    assert len(payload["args"]["demande"]) == 400
    assert payload["args"]["demande"].endswith("…")


# ---- fonctions de la table -------------------------------------------------------


def test_libelle_destructeur_par_mot_entier() -> None:
    assert is_destructive_label("Delete")
    assert is_destructive_label("Supprimer définitivement")
    assert not is_destructive_label("Removable Drives")
    assert not is_destructive_label("Deleted items")
    assert not is_destructive_label("")


def test_noms_d_application_comparables() -> None:
    assert app_keys("org.xfce.mousepad") == {"org.xfce.mousepad", "mousepad"}
    assert app_keys("Xfce4-terminal", "") == {"xfce4-terminal"}
    assert "gcr-prompter" in app_keys("Gcr-prompter.desktop")

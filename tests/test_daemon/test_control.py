"""Contrôle de l'ordinateur côté daemon : session (indicateur, arrêt garanti) et
boucle de l'orchestrator (une confirmation par requête, refus, arrêt en cours).

La vraie :class:`ControlSession` est branchée sur un bureau scripté et un faux
popup qui accuse l'affichage de l'indicateur (``control_ready``).
"""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from daemon import control as control_module
from daemon.control import ControlSession
from daemon.orchestrator import MAX_CONTROL_STEPS, Orchestrator, Plan, ToolCall
from permissions.audit_log import AuditLog
from permissions.confirmation import CONTROL_TOOL, ConfirmationResponse
from permissions.session_grants import SessionGrants
from tests.test_tools.computer_fakes import (
    FakeBackend,
    make_element,
    make_window,
    screen_tools,
)
from tools.computer.screen import Rect
from tools.computer.x11 import ControlStopped

BAR = [600, 920, 720, 90]
STOP = [990, 950, 30, 30]

# ---- doubles ---------------------------------------------------------------------


class _Popup:
    """Faux popup : reçoit les messages du daemon, accuse l'indicateur s'il le doit."""

    def __init__(self, *, connected: bool = True, acknowledges: bool = True) -> None:
        self.connected = connected
        self.acknowledges = acknowledges
        self.sent: list[dict] = []
        self.session: ControlSession | None = None

    def send(self, message: dict) -> bool:
        if not self.connected:
            return False
        self.sent.append(message)
        if (
            message.get("type") == "control_state"
            and message.get("active")
            and self.acknowledges
            and self.session is not None
        ):
            self.session.on_ready(
                {
                    "type": "control_ready",
                    "id": message["id"],
                    "stop_rect": STOP,
                    "bar_rect": BAR,
                }
            )
        return True

    def states(self) -> list[tuple[bool, str | None]]:
        return [
            (m["active"], m.get("reason"))
            for m in self.sent
            if m.get("type") == "control_state"
        ]


class _Listener:
    def __init__(self, on_escape: Callable[[], None]) -> None:
        self.on_escape = on_escape
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def _session(
    backend: FakeBackend | None = None, popup: _Popup | None = None, **kwargs: object
) -> tuple[ControlSession, _Popup, FakeBackend, list[_Listener]]:
    backend = backend or FakeBackend([make_window()])
    popup = popup or _Popup()
    listeners: list[_Listener] = []

    def factory(on_escape: Callable[[], None]) -> _Listener:
        listeners.append(_Listener(on_escape))
        return listeners[-1]

    session = ControlSession(
        popup.send,
        backend,
        listener_factory=factory,
        **kwargs,  # type: ignore[arg-type]
    )
    popup.session = session
    return session, popup, backend, listeners


# ---- session de contrôle -----------------------------------------------------------


def test_session_ouverte_une_fois_l_indicateur_affiche() -> None:
    session, popup, backend, listeners = _session()
    assert session.begin("m1") is None
    assert session.active and not session.cancelled
    assert popup.states() == [(True, None)]
    assert backend.guard is session and backend.prepared == 1
    assert session.stop_rect() == Rect(*STOP)
    assert backend.exclusions == [Rect(*BAR), Rect(*STOP)]
    assert listeners[0].started
    session.check()  # le garde laisse passer


def test_pas_de_popup_pas_de_controle() -> None:
    session, _, backend, _ = _session(popup=_Popup(connected=False))
    failure = session.begin("m1")
    assert failure is not None and "popup" in failure
    assert not session.active and backend.guard is None
    with pytest.raises(ControlStopped):
        session.check()


def test_indicateur_non_accuse_controle_annule() -> None:
    session, popup, _, _ = _session(
        popup=_Popup(acknowledges=False), ready_timeout=0.05
    )
    failure = session.begin("m1")
    assert failure is not None and "indicateur" in failure
    assert popup.states() == [(True, None), (False, "error")]
    assert not session.active


def test_accuse_d_une_autre_requete_ignore() -> None:
    session, _, _, _ = _session(popup=_Popup(acknowledges=False), ready_timeout=0.05)
    session._request_id = "m1"
    session.on_ready({"id": "autre", "stop_rect": STOP})
    assert not session._ready.is_set()


def test_arret_immediat_et_idempotent() -> None:
    session, popup, backend, _ = _session()
    session.begin("m1")
    session.cancel("user")
    session.cancel("user")
    assert session.cancelled and not session.active
    assert popup.states() == [(True, None), (False, "user")]
    assert backend.guard is None and backend.exclusions == []
    with pytest.raises(ControlStopped):
        session.check()


def test_fin_de_requete_eteint_l_indicateur() -> None:
    session, popup, backend, listeners = _session()
    session.begin("m1")
    session.end("finished")
    session.end("finished")
    assert popup.states() == [(True, None), (False, "finished")]
    assert listeners[0].stopped and backend.guard is None


def test_fin_apres_un_arret_ne_renvoie_rien() -> None:
    session, popup, _, _ = _session()
    session.begin("m1")
    session.cancel("user")
    session.end("user")
    assert popup.states() == [(True, None), (False, "user")]


def test_popup_perdu_arrete_le_controle() -> None:
    session, popup, _, _ = _session()
    session.set_popup_pid(321)
    session.begin("m1")
    assert session.myos_pids() == {321}
    session.popup_lost()
    assert session.cancelled and session.myos_pids() == set()
    assert popup.states()[-1] == (False, "error")


def test_echap_de_l_utilisateur_arrete_tout() -> None:
    session, _, _, listeners = _session()
    session.begin("m1")
    listeners[0].on_escape()
    assert session.cancelled


def test_echap_injectes_par_le_modele_decomptes() -> None:
    session, _, _, listeners = _session()
    session.begin("m1")
    session.expect_escape(2)
    listeners[0].on_escape()
    listeners[0].on_escape()
    assert not session.cancelled  # les deux Échap du modèle
    listeners[0].on_escape()
    assert session.cancelled  # le suivant vient de l'utilisateur


def test_echap_attendu_mais_perime(monkeypatch: pytest.MonkeyPatch) -> None:
    now = {"t": 100.0}
    monkeypatch.setattr(control_module.time, "monotonic", lambda: now["t"])
    session, _, _, listeners = _session()
    session.begin("m1")
    session.expect_escape()
    now["t"] += 5.0
    listeners[0].on_escape()
    assert session.cancelled


def test_une_seule_session_a_la_fois() -> None:
    session, _, backend, _ = _session()
    session.begin("m1")
    assert "déjà en cours" in (session.unavailable_reason() or "")
    assert "déjà en cours" in (session.begin("m2") or "")
    backend.reason = "session Wayland"
    assert session.unavailable_reason() == "session Wayland"


def test_ecoute_d_echap_impossible_le_bouton_reste() -> None:
    def broken(_on_escape: Callable[[], None]) -> _Listener:
        raise RuntimeError("pas de serveur X")

    popup = _Popup()
    session = ControlSession(
        popup.send, FakeBackend([make_window()]), listener_factory=broken
    )  # type: ignore[arg-type]
    popup.session = session
    assert session.begin("m1") is None and session.active


@pytest.mark.parametrize(
    "value", [None, [1, 2, 3], [1, 2, 0, 4], ["1", 2, 3, 4], [True, 2, 3, 4]]
)
def test_rectangle_du_popup_valide(value: object) -> None:
    assert control_module._rect(value) is None


# ---- orchestrator ---------------------------------------------------------------------


class _Model:
    def __init__(self, plans: list[Plan], *, repeat_last: bool = False) -> None:
        self.plans = list(plans)
        self.repeat_last = repeat_last
        self.calls = 0
        self.histories: list[list[dict]] = []

    def respond(self, messages: list[dict], on_token=None) -> Plan:
        _ = on_token
        self.histories.append(messages)
        self.calls += 1
        if self.plans:
            return (
                self.plans.pop(0)
                if not (self.repeat_last and len(self.plans) == 1)
                else self.plans[0]
            )
        return Plan(narration="Terminé.")


class _Confirmation:
    def __init__(self, decisions: list[str]) -> None:
        self.decisions = list(decisions)
        self.asked: list[dict] = []

    def ask(self, payload: dict) -> ConfirmationResponse:
        self.asked.append(payload)
        decision = self.decisions.pop(0) if self.decisions else "deny"
        return ConfirmationResponse(request_id=payload["request_id"], decision=decision)  # type: ignore[arg-type]


@pytest.fixture
def audit(tmp_path: Path) -> Iterator[AuditLog]:
    log = AuditLog(tmp_path / "audit.db")
    yield log
    log.close()


def _desktop_backend(label: str = "Enregistrer") -> FakeBackend:
    field = make_element(
        "text_field", "", Rect(620, 450, 600, 300), states=("focused",)
    )
    return FakeBackend(
        [make_window()],
        [make_element("button", label, Rect(700, 400, 80, 30)), field],
        focused=field,
    )


def _orchestrator(
    audit: AuditLog,
    plans: list[Plan],
    decisions: list[str],
    *,
    backend: FakeBackend | None = None,
    popup: _Popup | None = None,
    with_control: bool = True,
    repeat_last: bool = False,
):
    backend = backend or _desktop_backend()
    session, popup, _, listeners = _session(backend, popup)
    model = _Model(plans, repeat_last=repeat_last)
    confirmation = _Confirmation(decisions)
    orch = Orchestrator(
        model=model,
        tools=screen_tools(backend),
        grants=SessionGrants(),
        audit=audit,
        confirmation_provider=confirmation,
        control=session if with_control else None,
    )
    return orch, model, confirmation, session, popup, backend, listeners


def _call(tool: str, **args: object) -> Plan:
    return Plan(tool_calls=[ToolCall(tool=tool, args=dict(args))])


def _results(replies: list[dict]) -> list[tuple[str, str]]:
    return [(r["tool"], r["status"]) for r in replies if r["type"] == "tool_result"]


def test_une_seule_confirmation_par_requete(audit: AuditLog) -> None:
    orch, _, confirmation, _, popup, backend, _ = _orchestrator(
        audit,
        [
            _call("observe_screen"),
            _call("click", element=1),
            _call("type_text", text="ok"),
        ],
        ["approve_once"],
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "Enregistre le document"}, replies.append)
    assert len(confirmation.asked) == 1
    payload = confirmation.asked[0]
    assert payload["control"] is True and payload["risk_level"] == 2
    assert payload["args"] == {"demande": "Enregistre le document"}
    assert _results(replies) == [
        ("observe_screen", "ok"),
        ("click", "ok"),
        ("type_text", "ok"),
    ]
    assert popup.states() == [(True, None), (False, "finished")]
    assert ("click", 740, 415, "left", 1) in backend.actions
    rows = audit.fetch_all()
    assert rows[0]["tool"] == CONTROL_TOOL and rows[0]["decision"] == "approved"
    assert replies[-1]["type"] == "done"


def test_controle_refuse_jamais_redemande_dans_la_requete(audit: AuditLog) -> None:
    orch, _, confirmation, _, popup, backend, _ = _orchestrator(
        audit, [_call("observe_screen"), _call("click", element=1)], ["deny"]
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "clique"}, replies.append)
    assert len(confirmation.asked) == 1
    assert _results(replies) == [("observe_screen", "denied"), ("click", "denied")]
    assert popup.states() == [] and backend.actions == []
    assert audit.fetch_all()[0]["decision"] == "denied"


def test_controle_redemande_a_chaque_requete(audit: AuditLog) -> None:
    orch, _, confirmation, _, _, _, _ = _orchestrator(
        audit,
        [_call("observe_screen"), Plan(), _call("observe_screen")],
        ["approve_once", "approve_once"],
    )
    orch.handle({"id": "m1", "content": "regarde"}, lambda _r: None)
    orch.handle({"id": "m2", "content": "regarde encore"}, lambda _r: None)
    assert len(confirmation.asked) == 2


def test_controle_indisponible_sans_confirmation(audit: AuditLog) -> None:
    backend = _desktop_backend()
    backend.reason = "le contrôle de l'écran demande une session X11"
    orch, _, confirmation, _, _, _, _ = _orchestrator(
        audit, [_call("observe_screen")], [], backend=backend
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "regarde"}, replies.append)
    assert confirmation.asked == []
    (result,) = [r for r in replies if r["type"] == "tool_result"]
    assert result["status"] == "error" and "X11" in result["text"]


def test_controle_absent_de_l_installation(audit: AuditLog) -> None:
    orch, _, confirmation, _, _, _, _ = _orchestrator(
        audit, [_call("observe_screen")], [], with_control=False
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "regarde"}, replies.append)
    assert confirmation.asked == []
    assert _results(replies) == [("observe_screen", "error")]


def test_indicateur_impossible_rien_ne_s_execute(audit: AuditLog) -> None:
    orch, _, confirmation, _, _, backend, _ = _orchestrator(
        audit,
        [_call("click", element=1)],
        ["approve_once"],
        popup=_Popup(connected=False),
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "clique"}, replies.append)
    assert len(confirmation.asked) == 1
    assert _results(replies) == [("click", "error")]
    assert backend.actions == []
    assert audit.fetch_all()[0]["success"] is False


def test_arret_en_cours_de_route(audit: AuditLog) -> None:
    orch, model, _, session, popup, backend, _ = _orchestrator(
        audit,
        [
            _call("observe_screen"),
            Plan(
                tool_calls=[
                    ToolCall("click", {"element": 1}),
                    ToolCall("type_text", {"text": "jamais tapé"}),
                ]
            ),
        ],
        ["approve_once"],
    )
    clicked = backend.click

    def click_then_escape(x: int, y: int, **kwargs: object) -> None:
        clicked(x, y, **kwargs)  # type: ignore[arg-type]
        session.cancel("user")  # l'utilisateur appuie sur Échap

    backend.click = click_then_escape  # type: ignore[method-assign]
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "clique et tape"}, replies.append)
    assert ("click", 740, 415, "left", 1) in backend.actions
    assert not any(a[0] == "type" for a in backend.actions)
    assert _results(replies)[-1] == ("type_text", "denied")
    assert model.calls == 2  # le modèle n'est plus rappelé après l'arrêt
    assert popup.states() == [(True, None), (False, "user")]
    assert any("arrêté" in r.get("text", "") for r in replies if r["type"] == "notice")
    assert replies[-1]["type"] == "done"


def test_action_escaladee_redemandee_meme_sous_controle(audit: AuditLog) -> None:
    orch, _, confirmation, _, _, backend, _ = _orchestrator(
        audit,
        [_call("observe_screen"), _call("click", element=1)],
        ["approve_once", "approve_once"],
        backend=_desktop_backend("Supprimer"),
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "supprime"}, replies.append)
    assert [p.get("control", False) for p in confirmation.asked] == [True, False]
    assert confirmation.asked[1]["risk_level"] == 2
    assert ("click", 740, 415, "left", 1) in backend.actions


def test_plafond_d_etapes_eleve_pendant_le_controle(audit: AuditLog) -> None:
    orch, model, _, _, _, _, _ = _orchestrator(
        audit, [_call("observe_screen")], ["approve_once"], repeat_last=True
    )
    replies: list[dict] = []
    orch.handle({"id": "m1", "content": "regarde sans fin"}, replies.append)
    assert model.calls == MAX_CONTROL_STEPS
    assert any(
        str(MAX_CONTROL_STEPS) in r["text"] for r in replies if r["type"] == "notice"
    )


def test_seule_la_derniere_capture_reste_dans_l_historique(audit: AuditLog) -> None:
    orch, model, _, _, _, _, _ = _orchestrator(
        audit, [_call("observe_screen"), _call("observe_screen")], ["approve_once"]
    )
    orch.handle({"id": "m1", "content": "regarde deux fois"}, lambda _r: None)
    final = model.histories[-1]
    with_image = [m for m in final if m.get("image")]
    assert len(with_image) == 1 and with_image[0] is final[-1]

from telegram_commands import TelegramCommandHandler


def _callbacks(keyboard):
    return {
        button["callback_data"]
        for row in keyboard["inline_keyboard"]
        for button in row
    }


def test_option_navigation_contains_only_curated_routes():
    handler = TelegramCommandHandler("token", "42")
    # The option bootstrap installs its channel-specific report handler before
    # applying the whitelist/navigation profile.
    handler.register("report", lambda _="": "report")
    allowed = {
        "help", "menu", "status", "report", "signals", "all", "edge",
        "positions", "controlroom", "optionhealth", "optlots", "oisr",
        "oichart", "strikeflow", "pcr", "spreads", "direction",
        "nexttrade", "pause", "resume",
    }
    handler.restrict_to(allowed)
    handler.set_navigation_profile("option")

    sections = ("home", "monitor", "signals", "options", "control")
    callbacks = set().union(*(
        _callbacks(handler._menu_keyboard(section, "option"))
        for section in sections
    ))
    routed_commands = {
        value.split(":", 1)[1].split()[0]
        for value in callbacks
        if value.startswith(("cmd:", "prompt:", "confirm:"))
    }
    assert routed_commands <= set(handler._handlers)
    assert "cmd:dashboard" not in callbacks
    assert "prompt:optlots" in callbacks


def test_prompt_reply_continues_with_pending_command(monkeypatch):
    handler = TelegramCommandHandler("token", "42")
    received = []
    sent = []
    handler.register("optlots", lambda text: received.append(text) or "saved")
    monkeypatch.setattr(handler, "_api", lambda method, **params: {"ok": True})
    monkeypatch.setattr(
        handler, "send",
        lambda text, chat_id=None, reply_markup=None: sent.append(
            (text, chat_id, reply_markup)
        ) or True,
    )

    handler._handle_callback({
        "id": "cb1", "data": "prompt:optlots",
        "from": {"id": 42},
        "message": {"chat": {"id": 42}, "message_id": 10},
    })
    handler._handle_update({
        "update_id": 2,
        "message": {"text": "2", "from": {"id": 42}, "chat": {"id": 42}},
    })

    assert received == ["/optlots 2"]
    assert sent[-1][0] == "saved"
    assert "42" not in handler._pending_inputs


def test_menu_navigation_edits_existing_message(monkeypatch):
    handler = TelegramCommandHandler("token", "42")
    calls = []
    monkeypatch.setattr(
        handler, "_api",
        lambda method, **params: calls.append((method, params)) or {"ok": True},
    )
    monkeypatch.setattr(handler, "send", lambda *args, **kwargs: False)

    handler._handle_callback({
        "id": "cb2", "data": "menu:monitor",
        "from": {"id": 42},
        "message": {"chat": {"id": 42}, "message_id": 11},
    })

    assert any(method == "editMessageText" for method, _ in calls)


def test_callback_from_non_owner_is_rejected(monkeypatch):
    handler = TelegramCommandHandler("token", "42")
    sent = []
    monkeypatch.setattr(handler, "_api", lambda method, **params: {"ok": True})
    monkeypatch.setattr(
        handler, "send",
        lambda *args, **kwargs: sent.append((args, kwargs)) or True,
    )

    handler._handle_callback({
        "id": "cb3", "data": "cmd:status",
        "from": {"id": 99},
        "message": {"chat": {"id": 99}, "message_id": 12},
    })

    assert sent == []

# steven

The chat-widget **UI** app. A thin shell — no models, urls, or views.

Injects a hovering "Ask AI" chat window site-wide via a `FloatingPlugin`
(`plugins/floating_plugins.py`), rendered by `oya/base.html`'s
`{% render_floating_plugins %}`. The widget opens a websocket to the headless
`toto.sabbia` backend (`ws/sabbia/agent/<slug>/`) and chats with the agent that
`sabbia.PlatformChatbot` links to the active platform.

- Shown only to **logged-in** users (chat requires an authenticated socket).
- Renders nothing when sabbia isn't installed or no PlatformChatbot is configured.

## Deployment flag

`BUILD_STEVEN` installs this app and implies `BUILD_SABBIA` (the backend).

# Telegraph — Real-time Chat

Encrypted group chat backed by MLS (Messaging Layer Security). Used by the Enigma desktop app and accessible at `/telegraph/`.

## Architecture

- **Django Channels** WebSocket consumer (`consumers.py`) — relays messages between browser/desktop clients
- **MLS** end-to-end encryption via `rotor-wasm` (browser) / `rotor-core` Rust (Tauri)
- **JSON API** (`api_views.py`) — Bearer-token authenticated, consumed by Enigma Tauri app

## API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/telegraph/api/health/` | Service health check |
| POST | `/telegraph/api/login/` | Login → returns session token |
| POST | `/telegraph/api/logout/` | Logout |
| GET | `/telegraph/api/me/` | Current user profile |
| GET | `/telegraph/api/channels/` | List all channels |
| GET | `/telegraph/api/channels/{slug}/` | Channel detail + members |
| POST | `/telegraph/api/channels/{slug}/join/` | Join a channel |
| POST | `/telegraph/api/channels/{slug}/leave/` | Leave a channel |
| POST | `/telegraph/api/channels/leave-all/` | Leave all channels |
| POST | `/telegraph/api/channels/{slug}/upload/` | Upload image (base64-relayed via WS) |
| POST | `/telegraph/api/channels/{slug}/upload-audio/` | Upload audio (base64-relayed via WS) |

## WebSocket Message Types

| Type | Direction | Description |
|------|-----------|-------------|
| `chat_message` | both | Plaintext message |
| `image_message` | both | Base64 image data |
| `voice_message` | both | Base64 audio data (webm/ogg/mp4/wav) |
| `mls_app` | both | MLS-encrypted application message (opaque relay) |
| `mls_key_package` | both | MLS key package for handshake |
| `mls_welcome` | both | MLS welcome message |
| `mls_commit` | both | MLS commit message |
| `room_participants` | server→client | Active participant list update |
| `system_error` | server→client | Error notification |

## Testing

```bash
cd portal
python manage.py test toto.telegraph
```

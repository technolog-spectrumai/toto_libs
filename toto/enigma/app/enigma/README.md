# Enigma Tauri App

Enigma is a Tauri v2, Vue 3, Pinia, and Rust desktop client for the Enigma chat API.

## Profile-Specific Local State

The app reads two environment variables at startup:

- `APP_PROFILE`: local storage namespace for this app instance.
- `CHAT_USER`: default chat identity for MLS operations.

Persistent app state is stored by Rust/Tauri under the current profile:

```text
app_data_dir/profiles/{APP_PROFILE}/profile_state.json
```

That file contains the configured server URL, auth token, selected offline data profile, and offline profile data entries. New profiles use `CHAT_USER` as the default offline data profile name. MLS state is stored in the same profile directory with `mls_*.bin` files.

## Run Multiple Local Instances

Start the first instance:

```bash
APP_PROFILE=alice CHAT_USER=alice cargo tauri dev
```

Start a second instance from another terminal:

```bash
APP_PROFILE=bob CHAT_USER=bob cargo tauri dev --no-dev-server
```

Alice and Bob will use separate local files:

```text
profiles/alice/profile_state.json
profiles/bob/profile_state.json
```

This means Alice and Bob can have different server URLs, auth tokens, offline data profiles, and MLS state.

## Switch Local Profile At Runtime

Settings has an Offline Data card with a Local data profile field. Enter a profile name such as `alice` or `bob` and click Load profile.

Loading a profile updates the active Rust profile for this running app. Subsequent reads and writes use:

```text
app_data_dir/profiles/{profile_name}/profile_state.json
```

This gives the same storage separation as restarting with a different `APP_PROFILE`, without restarting the Tauri window. The active `CHAT_USER` value is set to the same name when switching this way.

## Test Offline Data Separation

1. Run Alice with `APP_PROFILE=alice CHAT_USER=alice`.
2. Open Settings.
3. Open Settings. Offline Data should default to an `alice` data profile.
4. Optionally set Data profile name to `Alice local data` and save it.
5. Open Profile, fill the offline profile form, and save it.
6. Run Bob with `APP_PROFILE=bob CHAT_USER=bob`.
7. Open Settings. Offline Data should default to a `bob` data profile.
8. Optionally set Data profile name to `Bob local data` and save it.
9. Open Profile, fill different offline data, and save it.
10. Switch between the two running app instances.

The Offline Data card also shows previous profile names as selectable chips, so you can quickly switch between saved local data profiles.

Expected result: Alice's offline data and Bob's offline data stay separate because each instance writes to its own `APP_PROFILE` directory.

## Test Server Connection

Settings uses:

```text
GET /enigma/api/health/
```

This endpoint is public, so connection testing works even before login.

# Authentication (AUTH.md)

Checked against the code at commit ed99084. Files named are under `skeleton_project/`.

## Summary

This project has **no user accounts**. There is no sign-up, email verification, login form,
password store, session, cookie, or password reset. It is a single-process Python service
for a Raspberry Pi (`skeleton_all_in_one_mqtt.py`) with no HTTP server. Nobody "logs in" to it.
The only identity checks are:

1. **The service authenticating to the MQTT broker** (`_configure_mqtt_security`,
   `skeleton_all_in_one_mqtt.py`): `client.username_pw_set(MQTT_USER, MQTT_PASS)` when
   `MQTT_USER` is set; optional TLS via `MQTT_TLS`, `MQTT_CA_CERT`, `MQTT_CERTFILE`, `MQTT_KEYFILE`
   (`client.tls_set`). Credentials come from environment variables (see ENV.md).
2. **The broker authenticating operators.** Operators (Home Assistant or any MQTT client) are
   trusted if the broker lets them publish to `holiday/<DEVICE_NAME>/.../set`. The service
   itself does **not** identify who sent a message (`_on_message`).
3. **Deployment** is run as root with `sudo` on the Pi (`scripts/deploy_release.py`); access is
   operating-system access (SSH/sudo), not an application login.

Visitors are never identified. Their speech is processed in memory and not persisted
(`LLM_MEMORY_TURNS` memory resets when a visit ends; see SECURITY.md).

| Topic you asked about | Status |
| --- | --- |
| Sign up / email verification | Not applicable: no accounts. |
| Login | Not applicable. Only the MQTT username/password above. |
| Lockout / slow-down after failed logins | Not applicable in-app. Broker-side only (not in this repo). |
| Session expiry / logout | Not applicable: no sessions. MQTT connection uses `keepalive=60`; `will_set` publishes `availability=offline` on loss. |
| Password reset / one-time links | Not applicable. The MQTT password is changed by editing the systemd override and restarting. |
| Password storage | Not applicable to users. The one secret, `MQTT_PASS`, lives in the systemd override (`systemd/override.conf.example` shows a placeholder only). |

## OWASP ASVS chapters 2 (Authentication) and 3 (Session Management)

Most requirements are out of scope because there are no human accounts or sessions. What remains
relevant is the machine-to-broker credential and the trust placed in the broker.

| ASVS area | Result |
| --- | --- |
| 2.1 Password security (length, no composition rules, paste allowed) | N/A, no user passwords. |
| 2.2 General authenticator security (rate limit, anti-automation) | N/A in-app. Rely on broker. |
| 2.3-2.6 Authenticator lifecycle, credential storage, out-of-band, look-up secrets | N/A. |
| 2.7-2.8 Out-of-band / one-time verifiers (OTP) | N/A. |
| 2.9 Cryptographic verifiers | N/A (the MQTT client certificate option `MQTT_CERTFILE`/`MQTT_KEYFILE` is the only one, and it is optional). |
| 2.10 Service authentication (no hard-coded secrets, stored securely) | Partly met: secrets are read from the environment, none committed. See Gaps found. |
| 3.x Session management (tokens, timeout, logout, cookies) | N/A, no sessions. |

## Gaps found

1. **TLS is off by default.** `MQTT_TLS` defaults to `0` and the default port is 1883
   (`skeleton_all_in_one_mqtt.py`). On that default the MQTT password and every command travel in
   clear text. The override example only has TLS commented out. (ASVS 2.10 / 9.1.)
2. **TLS server verification depends on `MQTT_CA_CERT`.** When `MQTT_TLS=1` and no CA is set, `tls_set`
   uses the system default CAs; there is no `tls_insecure_set(False)` call or pinning, and no check
   that the CA file exists.
3. **No per-sender identity or authorisation.** Any client allowed by the broker can send any
   command, including `restart/set`, `maintenance/set`, `content/reload/set`, and calibration
   topics. The service cannot tell operators apart or enforce roles (see RLS_POLICIES.md).
4. **MQTT credentials live in plaintext in the systemd override.** The file should be mode 0600
   root-owned; nothing in the repo enforces that (`INSTALL.txt` is guidance only).
5. **No `.env.example`** is committed. The placeholder values live in `systemd/override.conf.example`
   instead (see ENV.md).
6. **Default client id is predictable** (`holiday_<DEVICE_NAME>`), and the default `MQTT_USER`
   placeholder `<Username>` and `MQTT_HOST` `<ipAddress>` are used silently if the variables are unset;
   there is no startup refusal when credentials are missing.

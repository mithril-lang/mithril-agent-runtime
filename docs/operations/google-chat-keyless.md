# Keyless Google Chat gateway credentials

Use a dedicated Cloud SDK configuration for the Google Chat gateway. Do not replace the machine's default Application Default Credentials (ADC), which may belong to another account or workload.

1. Enable Google Chat, Pub/Sub, and IAM Service Account Credentials APIs in the selected project. Create a service account without a key. Grant it Pub/Sub Subscriber and Viewer on the pull subscription. Grant the Google Chat service identity shown in the Chat API configuration Pub/Sub Publisher on the topic.
2. Grant the gateway operator Service Account Token Creator on that service account. In a private `CLOUDSDK_CONFIG` directory, run `gcloud auth application-default login --impersonate-service-account=SA_EMAIL` and select the approved operator account during sign-in. Protect the resulting `application_default_credentials.json` with mode 0600.
3. Install the locked gateway runtime with the `google-chat` extra. Configure only the intended gateway profile with `GOOGLE_CHAT_PROJECT_ID`, `GOOGLE_CHAT_SUBSCRIPTION_NAME`, `GOOGLE_CHAT_SERVICE_ACCOUNT_JSON` pointing at that private file, and `GOOGLE_CHAT_ALLOWED_USERS` naming the approved users. Keep `GOOGLE_CHAT_ALLOW_ALL_USERS` unset.
4. Confirm a restricted test DM, Pub/Sub delivery, and an actual bot reply before adding the app to a shared space. Adding a bot to a shared space gives it access to that space's messages and requires a separate access review.

The adapter loads the impersonated ADC credential type from the explicit file and obtains short-lived `chat.bot` and Pub/Sub tokens. It does not fall back to another profile's credentials when an explicit path is set. Never commit credentials, authorization codes, or token output.

## Personal DM bridge to an existing local gateway

When a multiplex gateway already serves other profiles, run `python -m gateway.google_chat_api_bridge` from a separate locked runtime with the `google-chat` extra. It listens to the Chat Pub/Sub subscription and sends only one approved user's direct messages to the existing gateway's `127.0.0.1:8642/v1/chat/completions` endpoint. It does not change or restart that gateway. Group messages and other senders are discarded.

Set `HERMES_HOME` to a private bridge-only state directory, plus `GOOGLE_CHAT_PROJECT_ID`, `GOOGLE_CHAT_SUBSCRIPTION_NAME`, `GOOGLE_CHAT_SERVICE_ACCOUNT_JSON` (the private impersonated ADC file), `GOOGLE_CHAT_BRIDGE_ALLOWED_EMAIL` (exactly one email address), and `GOOGLE_CHAT_BRIDGE_API_ENV_FILE` (the existing gateway's mode-0600 `.env` holding `API_SERVER_KEY`). Keep the API server bound to loopback. The bridge reads the key in memory and never stores a copy. Each approved DM has a stable, isolated API session; Pub/Sub and the Chat API remain under the bot identity.

Verify the running gateway has a usable API key and that its health endpoint is local before starting the bridge. After startup, send a text DM and confirm the answer came from the local gateway, then check that the bridge remains connected. The Google Chat adapter acknowledges inbound Pub/Sub messages before the agent turn settles, so a failed local API call returns an explicit retry message when delivery is still possible; this sidecar is a personal pilot, not a durable queue for regulated workflows.

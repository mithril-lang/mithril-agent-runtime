# Keyless Google Chat gateway credentials

Use a dedicated Cloud SDK configuration for the Google Chat gateway. Do not replace the machine's default Application Default Credentials (ADC), which may belong to another account or workload.

1. Enable Google Chat, Pub/Sub, and IAM Service Account Credentials APIs in the selected project. Create a service account without a key. Grant it Pub/Sub Subscriber and Viewer on the pull subscription. Grant the Google Chat service identity shown in the Chat API configuration Pub/Sub Publisher on the topic.
2. Grant the gateway operator Service Account Token Creator on that service account. In a private `CLOUDSDK_CONFIG` directory, run `gcloud auth application-default login OPERATOR_EMAIL --impersonate-service-account=SA_EMAIL`. Protect the resulting `application_default_credentials.json` with mode 0600.
3. Configure only the intended gateway profile with `GOOGLE_CHAT_PROJECT_ID`, `GOOGLE_CHAT_SUBSCRIPTION_NAME`, `GOOGLE_CHAT_SERVICE_ACCOUNT_JSON` pointing at that private file, and `GOOGLE_CHAT_ALLOWED_USERS` naming the approved users. Keep `GOOGLE_CHAT_ALLOW_ALL_USERS` unset.
4. Confirm a restricted test DM, Pub/Sub delivery, and an actual bot reply before adding the app to a shared space. Adding a bot to a shared space gives it access to that space's messages and requires a separate access review.

The adapter loads the impersonated ADC credential type from the explicit file and obtains short-lived `chat.bot` and Pub/Sub tokens. It does not fall back to another profile's credentials when an explicit path is set. Never commit credentials, authorization codes, or token output.

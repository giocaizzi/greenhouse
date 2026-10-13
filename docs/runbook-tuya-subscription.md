# Runbook: Tuya IoT Core subscription expired

**Symptom.** A critical `tuya_subscription_expired` alert in the inbox (and an ntfy push). Sensor sync stops bringing new readings, and the Cloud on/off pulse to the irrigators fails. The server log shows `subscription has expired` (OpenAPI code `28841002`, or tinytuya `Err 913`).

**Cause.** The free IoT Core plan of the Tuya Cloud project has a fixed term. When it lapses, every Cloud call fails.

## Fix

1. Sign in at [iot.tuya.com](https://iot.tuya.com) and open **Cloud → Development → your project → Service API**.
2. Click **IoT Core → Extend** and submit the extension form. Review can take a day or more.
3. Wait until the plan shows **In service**.
4. Run `greenhouse sync` or wait for the next sync job. A sync with no errors resolves the alert by itself.
5. If the alert stays open, read the sync errors with `greenhouse sync`. Any other error keeps the alert open, so fix that error first.

## Prevention

Write down the new expiry date the console shows, and set a reminder a week before it.

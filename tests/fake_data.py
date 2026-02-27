"""Shared fake test data for the tuya_irrigation test suite.

All values here are deliberately fake/placeholder.
RFC 5737 IPs (192.0.2.x) are reserved for documentation and testing.
"""

# --- Fake Tuya credentials ---
FAKE_CLIENT_ID = "fake_client_id_for_tests"
FAKE_CLIENT_SECRET = "fake_client_secret_for_tests"
FAKE_REGION = "eu"

# --- Fake device identifiers ---
FAKE_DEVICE_ID = "fake_tuya_device_aabbccdd"
FAKE_SENSOR_ID = "fake_tuya_sensor_aabbccdd"
FAKE_DEVICE_ID_2 = "fake_tuya_device_eeff0011"

# --- Fake network config (RFC 5737: 192.0.2.x reserved for docs/tests) ---
FAKE_DEVICE_IP = "192.0.2.1"
FAKE_LOCAL_KEY = "fakelocalkeyxxxx"

# --- Fake cluster/plant data ---
FAKE_CLUSTER_NAME = "Test Cluster"
FAKE_CLUSTER_LOCATION = "Test Location"
FAKE_PLANT_SPECIES = "Monstera deliciosa"
FAKE_IRRIGATOR_NAME = "Test Irrigator"
FAKE_SENSOR_NAME = "Test Sensor"

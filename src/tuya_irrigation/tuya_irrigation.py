#!/usr/bin/env python3
"""CLI wrapper for Tuya device control using tinytuya."""

import argparse
import json
import os
import sys

import tinytuya


def get_device():
    """Initialize Tuya device from environment variables."""
    device_id = os.environ.get("TUYA_DEVICE_ID")
    device_ip = os.environ.get("TUYA_DEVICE_IP")
    local_key = os.environ.get("TUYA_LOCAL_KEY")
    region = os.environ.get("TUYA_REGION", "eu")
    
    if not device_id:
        print("ERROR: TUYA_DEVICE_ID not set", file=sys.stderr)
        sys.exit(1)
    
    # Determine if we should use local or cloud
    use_local = device_ip and local_key
    
    if use_local:
        # Local mode (faster, more reliable)
        device = tinytuya.OutletDevice(device_id, device_ip, local_key)
        device.set_version(3.3)
    else:
        # Cloud mode
        client_id = os.environ.get("TUYA_CLIENT_ID")
        client_secret = os.environ.get("TUYA_CLIENT_SECRET")
        
        if not all([client_id, client_secret]):
            print("ERROR: TUYA_CLIENT_ID or TUYA_CLIENT_SECRET not set", file=sys.stderr)
            sys.exit(1)
        
        cloud = tinytuya.Cloud(
            apiRegion=region,
            apiKey=client_id,
            apiSecret=client_secret,
        )
        device = cloud.getdevice(device_id)
    
    return device, use_local


def cmd_status(device, use_local):
    """Get device status."""
    try:
        if use_local:
            status = device.status()
        else:
            status = device.status()  # Cloud API
        
        print(json.dumps(status, indent=2))
        
        # Parse for human-readable output
        if "dps" in status:
            dps = status["dps"]
            switch_on = dps.get("1", False)
            timer_left = dps.get("11", 0)  # Timer remaining in seconds
            print(f"\nState: {'ON' if switch_on else 'OFF'}")
            if timer_left > 0:
                print(f"Time remaining: {timer_left // 60} min")
        
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def cmd_on(device, use_local):
    """Turn device ON."""
    try:
        if use_local:
            device.turn_on()
        else:
            device.turn_on()
        print("Device turned ON")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def cmd_off(device, use_local):
    """Turn device OFF."""
    try:
        if use_local:
            device.turn_off()
        else:
            device.turn_off()
        print("Device turned OFF")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def cmd_start(device, use_local, minutes):
    """Start irrigation with timer."""
    try:
        # Turn on switch (DP 1)
        if use_local:
            device.set_value(1, True)
            # Set timer in seconds (DP 11)
            device.set_value(11, minutes * 60)
        else:
            device.turn_on()
            # Cloud API for timer (adjust based on device)
            device.set_value(11, minutes * 60)
        
        print(f"Irrigation started for {minutes} minutes")
        return 0
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description="Tuya device control CLI")
    parser.add_argument("command", choices=["status", "on", "off", "start", "local"])
    parser.add_argument("subcommand", nargs="?", help="Subcommand for 'local' mode")
    parser.add_argument("--minutes", type=int, help="Duration for 'start' command")
    
    # Parse known args to handle flexible ordering
    args, unknown = parser.parse_known_args()
    
    # If there are unknown args and --minutes wasn't parsed, try to find it
    if unknown and not args.minutes:
        i = 0
        while i < len(unknown):
            if unknown[i] == "--minutes" and i + 1 < len(unknown):
                args.minutes = int(unknown[i + 1])
                break
            i += 1
    
    device, use_local = get_device()
    
    # Handle 'local' mode prefix
    if args.command == "local":
        if not args.subcommand:
            parser.error("'local' requires a subcommand (status, on, off, start)")
        command = args.subcommand
        use_local = True  # Force local mode
    else:
        command = args.command
    
    # Execute command
    if command == "status":
        return cmd_status(device, use_local)
    elif command == "on":
        return cmd_on(device, use_local)
    elif command == "off":
        return cmd_off(device, use_local)
    elif command == "start":
        if not args.minutes:
            parser.error("'start' requires --minutes argument")
        return cmd_start(device, use_local, args.minutes)
    else:
        parser.error(f"Unknown command: {command}")


if __name__ == "__main__":
    sys.exit(main())

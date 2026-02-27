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
        device.set_version(3.5)  # Rainpoint IK10PW requires protocol v3.5
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
    # Simple manual parsing for "local" prefix
    args_list = sys.argv[1:]
    use_local_mode = False
    
    if args_list and args_list[0] == "local":
        use_local_mode = True
        args_list = args_list[1:]  # Remove "local" prefix
    
    # Now parse the actual command
    parser = argparse.ArgumentParser(description="Tuya device control CLI")
    parser.add_argument("command", choices=["status", "on", "off", "start"])
    parser.add_argument("--minutes", type=int, help="Duration for 'start' command")
    
    args = parser.parse_args(args_list)  # Parse from cleaned args_list
    
    device, use_local = get_device()
    
    # Override use_local if "local" prefix was given
    if use_local_mode:
        use_local = True
    
    # Execute command
    command = args.command
    
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

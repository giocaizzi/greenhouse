#!/usr/bin/env node
/*
Tuya Irrigation (RainPoint IK10PW / EU-SD001-M) — LOCAL control via tuyapi.

Env required:
  TUYA_DEVICE_ID
  TUYA_LOCAL_KEY
  TUYA_DEVICE_IP
Optional:
  TUYA_DEVICE_VERSION (default 3.5)

DP mapping (confirmed on RainPoint WiFi Watering Pump ggq):
  1   switch (bool)
  102 how_long_seconds (int)
  103 how_often_hours (int)
  104 remaining_seconds (int)
  106 working_status (enum string)  "1"=idle, "3"=running (observed)
  107 next_irrigation_minutes (int)
  109 auto_run (bool)

This script prints only derived values (no keys).
*/

const TuyAPI = require('tuyapi');

function must(name) {
  const v = process.env[name];
  if (!v) {
    console.error(`Missing env var: ${name}`);
    process.exit(1);
  }
  return v;
}

function withTimeout(p, ms, label) {
  return Promise.race([
    p,
    new Promise((_, rej) => setTimeout(() => rej(new Error(`Timeout ${label} (${ms}ms)`)), ms)),
  ]);
}

async function getDP(device, dp) {
  // tuyapi returns raw value if you request a single dp
  return await withTimeout(device.get({ dps: dp }), 6000, `get(dp=${dp})`);
}

async function setDP(device, dp, value) {
  // tuyapi set: { dps: <number|string>, set: <value> }
  return await withTimeout(device.set({ dps: dp, set: value }), 6000, `set(dp=${dp})`);
}

function fmtHMM(totalMinutes) {
  const m = Math.max(0, Number(totalMinutes) || 0);
  const h = Math.floor(m / 60);
  const mm = m % 60;
  return `${h}h${String(mm).padStart(2, '0')}m`;
}

function fmtMS(totalSeconds) {
  const s = Math.max(0, Number(totalSeconds) || 0);
  const m = Math.floor(s / 60);
  const ss = s % 60;
  return `${m}m${String(ss).padStart(2, '0')}s`;
}

async function main() {
  const id = must('TUYA_DEVICE_ID');
  const key = must('TUYA_LOCAL_KEY');
  const ip = must('TUYA_DEVICE_IP');
  const version = process.env.TUYA_DEVICE_VERSION || '3.5';

  const device = new TuyAPI({ id, key, ip, version, issueGetOnConnect: false, timeout: 10 });
  device.on('error', (e) => console.error('device error:', e.message || e));

  const [cmd, ...rest] = process.argv.slice(2);
  if (!cmd || ['status','on','off','set'].indexOf(cmd) === -1) {
    console.log('Usage: tuya_irrigation_local.js <status|on|off|set> [--minutes N] [--every-hours H] [--auto-run true|false]');
    process.exit(cmd ? 1 : 0);
  }

  await withTimeout(device.connect(), 8000, 'connect');

  try {
    if (cmd === 'status') {
      const sw = await getDP(device, 1);
      const howLong = await getDP(device, 102);
      const howOften = await getDP(device, 103);
      const remaining = await getDP(device, 104).catch(() => null);
      const wstat = await getDP(device, 106).catch(() => null);
      const nextMin = await getDP(device, 107).catch(() => null);
      const autoRun = await getDP(device, 109).catch(() => null);

      const running = String(wstat) === '3' || sw === true;

      console.log('── Irrigation (LOCAL) ───────────────────────');
      console.log(`  Switch            : ${sw ? 'ON' : 'OFF'}`);
      console.log(`  Working status    : ${wstat ?? '?' } ${running ? '(running)' : '(idle)'}`);
      console.log(`  How long          : ${howLong}s (${fmtMS(howLong)})`);
      console.log(`  How often         : ${howOften}h`);
      if (remaining !== null) console.log(`  Remaining         : ${remaining}s (${fmtMS(remaining)})`);
      if (nextMin !== null) console.log(`  Next irrigation   : ${nextMin} min (${fmtHMM(nextMin)})`);
      if (autoRun !== null) console.log(`  Auto run          : ${autoRun ? 'true' : 'false'}`);
      console.log('────────────────────────────────────────────');
    }

    if (cmd === 'on') {
      await setDP(device, 1, true);
      console.log('✅ Switch ON (local)');
    }

    if (cmd === 'off') {
      await setDP(device, 1, false);
      console.log('✅ Switch OFF (local)');
    }

    if (cmd === 'set') {
      // simple arg parsing
      const args = new Map();
      for (let i=0;i<rest.length;i++) {
        if (rest[i].startsWith('--')) {
          args.set(rest[i], rest[i+1]);
          i++;
        }
      }

      if (args.has('--minutes')) {
        const minutes = Number(args.get('--minutes'));
        const seconds = Math.max(0, Math.floor(minutes * 60));
        await setDP(device, 102, seconds);
        console.log(`✅ Set how-long = ${minutes} min (${seconds}s)`);
      }

      if (args.has('--every-hours')) {
        const hours = Number(args.get('--every-hours'));
        await setDP(device, 103, Math.max(0, Math.floor(hours)));
        console.log(`✅ Set how-often = ${hours} h`);
      }

      if (args.has('--auto-run')) {
        const v = String(args.get('--auto-run')).toLowerCase();
        const b = (v === 'true' || v === '1' || v === 'yes' || v === 'on');
        await setDP(device, 109, b);
        console.log(`✅ Set auto-run = ${b}`);
      }
    }
  } finally {
    await withTimeout(device.disconnect(), 4000, 'disconnect').catch(() => {});
  }
}

main().catch((e) => {
  console.error('Fatal:', e.message || e);
  process.exit(1);
});

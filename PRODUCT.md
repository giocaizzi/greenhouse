# Product

## Register

product

## Users

Primary: one technical adult (the owner) running this on a home server — comfortable with data-dense views, always authenticated, expects precision.

Secondary: household members (partner, family) who may check plant status or irrigation history from the same network. Less comfortable with raw numbers; need readable context, not just values.

Context: home. Used on desktop or phone while near the plants, or at a glance from across the room. Never in a high-stakes commercial environment — but the hardware actuates real irrigation, so errors are costly (drowned or dry plants).

## Product Purpose

A personal smart irrigation controller. It reads Tuya sensors, decides when and how long to water each plant cluster, fires the irrigators over local protocol, and learns from past cycles to surface efficiency alerts. The web UI is the household's window into that system — at a glance for the secondary user, in depth for the primary.

Success looks like: waking up and knowing at a glance that plants are fine, or immediately knowing which one needs attention and why.

## Brand Personality

Precise, calm, botanical.

The interface should feel like a well-designed scientific instrument that happens to live in your living room. Confidence in the data, never alarmist, never casual. The green accent is intentional — it means health, not decoration.

## Anti-references

- Terminal / hacker aesthetic: no green-on-black, no ASCII art, no blinking cursors, no dev-tool cosplay. The secondary user should not feel like they've accidentally opened a server console.
- Generic SaaS dashboard scaffolding: no hero-metric templates, no identical card grids, no "01 / 02 / 03" section counters, no eyebrow labels on every section.
- Gardening lifestyle apps: no soft pastels, no Instagram-plant-mom roundness, no oversized leaf illustrations as the primary aesthetic.

## Design Principles

1. **The plant is the unit of truth.** Every screen should make it clear which plant or cluster is being discussed, and what its current state is. Numbers serve that story; they're not the hero themselves.
2. **Calm by default, urgent when needed.** The ambient state should feel settled. Alerts and danger states should stand out precisely because the rest doesn't shout.
3. **Legible to a non-expert at a glance.** The primary user can read raw sensor values; the household user needs readable verdicts. Design supports both without patronizing either.
4. **Actuation is deliberate.** Controls that fire real hardware (irrigate, start, stop) must feel weighty and intentional — not easily triggered, always confirming what just happened.
5. **Local-first, always-available.** No spinners waiting on cloud APIs. The UI should feel as fast and reliable as the home network it runs on.

## Accessibility & Inclusion

WCAG 2.1 AA. Focus: keyboard navigation, focus-visible on all interactive elements, ≥4.5:1 body text contrast, ≥3:1 large text, reduced-motion alternatives for all animations. Color alone must never be the sole indicator of status (badges and pills already pair color with text/icon).

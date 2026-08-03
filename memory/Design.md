# Design

_Full design system arrives with Phase 4. This doc pins the direction
so we don't drift into AI-slop defaults._

## Direction
Editorial, quiet, engineering-college-serious but not corporate. The
audience is students who are stressed about placements — the app
should feel calm, credible, and fast, not "startup-y".

## What we're deliberately NOT doing
- No purple/violet gradients on white.
- No Inter / Roboto / system-sans-only stack.
- No centered hero with three feature cards.
- No emoji as UI icons.

## Palette (working)
- `--pp-paper` `#fbfaf7` — background, warm off-white
- `--pp-ink`   `#14140f` — primary text, near-black with a green cast
- `--pp-muted` `#6b6a5a` — meta, timestamps, eyebrows
- `--pp-line`  `#d9d5c6` — hairlines, chip borders
- `--pp-moss`  `#7a9a3a` — status/positive accent
- `--pp-amber` `#c68a2f` — "new since last visit" highlight
- Dark theme: swap paper→`#111110`, ink→`#f2efe6`, keep moss/amber.

## Typography
- **Display / headings:** `Fraunces` (serif, variable) — set at 500,
  tight tracking, editorial feel.
- **Body:** `IBM Plex Sans` — reads well on Android WebView, distinctive.
- **Mono / eyebrows / slno chips:** `JetBrains Mono`.

Hierarchy:
- H1: `clamp(2.25rem, 5vw, 3.75rem)`, Fraunces 500
- H2: `text-lg` on mobile, `text-xl` at md
- Body: `1rem` / 1.55
- Meta: `0.85rem`, Plex Sans, `--pp-muted`

## Layout
- Left-aligned, single column on mobile.
- Desktop: max-width ~ 720px content column, generous left margin (not
  centered).
- 2–3× the whitespace instinct says.

## Icons
- Lucide React only. Stroke 1.5. Never emoji.

## Motion
- Micro-transitions on hover/press only (opacity, transform).
- Notice list: staggered fade-in of the first 8 items, then no
  animation on scroll.
- Notification arrival: subtle amber pulse on the affected list item.

## Component sources
- shadcn/ui primitives in `/app/frontend/src/components/ui/`.
- `sonner` for toasts.
- Custom notice-card component (Phase 4) built on shadcn `Card`.

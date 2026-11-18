# PBO design system

PBO's interface is sober and dense: neutral greys, one accent, little
decoration. This page is the reference for anyone changing the web UI
(`apps/web`, the `.pbo-ui` layer). The tokens live in
`apps/web/src/ui/ui.css`; Tailwind maps them in `apps/web/tailwind.config.js`
(`bg-surface`, `text-muted`, `bg-accent`…). Never write a raw colour in a
component.

Validated references: `docs/design/mockups/*.html` (open
`live-run-modal.html` directly in a browser; it has a light/dark switch).

## Colour

Neutral greys plus ONE steel-blue accent. Status colours are desaturated.
Each text pair below meets WCAG AA (≥ 4.5:1). Dots, rings and segments meet
≥ 3:1 against the surface.

| Token | Use | Light | Dark |
| --- | --- | --- | --- |
| `bg` | page background | `#F7F7F8` | `#0D0E10` |
| `surface` | cards, dialogs, menus | `#FFFFFF` | `#141518` |
| `subtle` | table header, inputs | `#F3F3F5` | `#1A1B1F` |
| `hover` | hovered/selected row, pressed chip | `#ECECEF` | `#212227` |
| `border` | hairlines, card borders | `#E4E4E8` | `#27282D` |
| `border-strong` | input borders, pressed chip | `#D2D3D8` | `#36383E` |
| `fg` | text | `#17181C` (17.7:1) | `#ECEDEF` (15.6:1) |
| `muted` | secondary text | `#585B63` (6.8:1) | `#A3A6AE` (7.5:1) |
| `faint` | labels, timestamps | `#6B6E77` (5.1:1) | `#878A93` (5.3:1) |
| `accent` | primary button, progress | `#2F5D8A` (white text 6.9:1) | `#3A6A9E` (white text 5.6:1) |
| `accent-text` | links, accent text | `#2B5782` (7.5:1) | `#8DB3DB` (8.4:1) |
| `focus` | focus ring | `#2F5D8A` | `#5C8CC2` (5.2:1) |
| `success` / `success-text` | done | `#3F8A5F` / `#2E6B48` (6.3:1) | `#5FA37A` / `#7DBB95` (8.2:1) |
| `warning` / `warning-text` | attention | `#B5862E` / `#87601A` (5.7:1) | `#C9A15A` / `#D2AE6A` (8.7:1) |
| `danger` / `danger-text` | failure, destructive | `#B4453F` (white 5.4:1) / `#A23D3A` (6.4:1) | `#B4504B` (white 5.0:1) / `#E38E8A` (7.4:1) |
| `console` / `console-fg` | run journal | `#F4F6F9` / `#2A2C32` (12.9:1) | `#0A0B0D` / `#D4D6DB` (13.5:1) |

The accent is reserved for four things:
- the primary button;
- the focus ring;
- progress (bars, rings, segments);
- links.

Never use it for decoration: no tinted backgrounds, tiles or chips. The
disk colours (`diskProfile.COLOR_CLASS`) are the disks' identity, chosen by
the operator; they are not theme colours.

### Accents (Settings › Appearance, Phase 5j)

Steel blue is the default. Five predefined alternatives replace `accent`,
`accent-text` and `focus` (`ui.css`, `[data-accent]`). Every pair is AA:
white text on the accent ≥ 4.7:1, accent text ≥ 6.8:1, focus ≥ 5.7:1.

| Accent | Light accent / text | Dark accent / text / focus |
| --- | --- | --- |
| steel | `#2F5D8A` / `#2B5782` | `#3A6A9E` / `#8DB3DB` / `#5C8CC2` |
| teal | `#1F6B6B` / `#1C6363` | `#2A7A7A` / `#7CC4C2` / `#4FA3A1` |
| green | `#2F6B45` / `#2A6340` | `#3A7A52` / `#86C49B` / `#5AA676` |
| amber | `#8A5A12` / `#7D5210` | `#9A6A1E` / `#E0B36A` / `#C99A4A` |
| rose | `#A13A55` / `#963550` | `#A84862` / `#EB9AB0` / `#D27891` |
| violet | `#5E4A9E` / `#574394` | `#6B57AA` / `#B5A6EA` / `#9585D6` |

Other appearance settings, applied as attributes on the `.pbo-ui` root by
`ui/AppearanceProvider.tsx`:

- **Density** "compact" draws the page content at 90 %
  (`[data-shell-content]`); the shell keeps its size.
- **Collapsed menu** shows icons only, with each label as a tooltip.
- **Reduce animations**: "system" follows the OS, "yes" (`data-motion=reduce`)
  always, "no" (`data-motion=full`) never.

## Typography

- **Font**: Geist for everything. Figures are tabular everywhere
  (`font-variant-numeric: tabular-nums` on `.pbo-ui`).
- **Sizes**: body 14 px, secondary 13 px, labels 12 px. Page title 20 px
  (`PageTitle`); section title 14–15 px, semibold.
- **Monospace**: Geist Mono, ONLY for the run journal, serials, UPIDs,
  paths, commands and technical ids (`vm/100`, job ids, URLs). Never for
  sizes, percentages, durations or counts.

## Spacing and shape

- **Grid**: 4 px. Use paddings and gaps from 4, 8, 12, 16 and 20 px; 2 px
  half steps only inside controls.
- **Radius**: 6 px for controls and chips, 8 px for menus and inputs,
  12 px for cards and dialogs.
- **Fewer cards inside cards**: inside a card, separate lists and sections
  with hairlines (`border-t border-border`), not nested tinted boxes.

## States

- **« ● text »** (`ui/StatusDot.tsx`): a 7 px dot and the text, no pill.
  Used for disk states, protection, agent update states, SMART, and the
  "new disk" count in the sidebar.
- **Badges**: kept for exceptions only (a disk alert, "recommended"). A
  card's tags are plain text joined by " · ".
- **Status glyphs** (`ui/StatusIcon.tsx`): ONE component for everything
  that runs. It is used in the run modal (header, resources), the
  dashboard's last runs, Activity, disk history, toasts and the top-bar pill.

  | Kind | Glyph |
  | --- | --- |
  | `running` | ring that fills with `progress` (turns when unknown) |
  | `success` | full disc + thin check |
  | `failed` | full disc + cross |
  | `skipped` | dotted ring |
  | `pending` | empty ring |

## Icons

- **Style**: lucide icons at 16 px, stroke 1.5 (set globally on
  `svg.lucide`).
- **No tiles**: no icon inside a rounded square — not in titles, dialogs or
  the logo. The disk avatar is the exception: it is the disk's identity
  (colour or photo).

## Page titles and help

- **No "i" button** next to a title. The page's help is a tooltip on the
  title itself (`ui/PageTitle.tsx`, hover and keyboard focus).
- **Longer help**: use a "Learn more" link.

## Tables

- **Header**: normal case, `bg-subtle`, `text-muted`, 13 px, follows the
  theme.
- **Rows**: separated by hairlines.
- **Legacy CSS**: the legacy `styles.css` styles every `th`/`td`. `ui.css`
  neutralises that inside `.pbo-ui`, so a new table never inherits the old
  dark uppercase header.

## Logo

- **Mark**: two arcs (disks in rotation) around a spindle. Monochrome
  (`currentColor`), next to the word "PBO" (`ui/Logo.tsx`).
- **Files**: `docs/design/logo-mark.svg` and `docs/design/logo.svg`.
- **Favicon**: `apps/web/public/favicon/favicon.svg` follows the browser
  theme. The PNG/ICO versions and the manifest icons use the light mark on
  `#17181C`.

## Run modal

- **Header**: `StatusIcon`, the title, then ONE secondary line:
  type · time · disk (with its colour).
- **Progress**: the big percentage with a sans-serif stats line. Below it,
  ONE segmented bar (`StepBar`) with one segment per step:

  | Segment | Meaning |
  | --- | --- |
  | filled | done (green once the run succeeded) |
  | animated | in progress (the copy fills with its own %) |
  | hatched | skipped |
  | red | failed |

  Hovering a segment shows its name and duration (`runModel.stepTimings`).
  One line follows: « Étape 5 sur 7 · Nettoyage · rétention et GC · 42 s ».
- **Details**: the source → target route and the run id sit in a folded
  "Details" block.
- **Journal**: follows the theme (tinted panel in light mode). Level colours
  are the status tokens.
- **Focus**: the dialog takes the focus itself on open and when the run
  ends; the shared `ui/CloseButton.tsx` shows a ring on keyboard focus only.

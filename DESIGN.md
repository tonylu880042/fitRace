# Design System: FitRaceStudio Hyper-Performance Theme
**Project ID:** projects/fitrace-hyper-performance-theme

## 1. Visual Theme & Atmosphere
* **Aesthetic Philosophy**: Heavy industrial, raw, and high-energy gritty styling. Drawing inspiration from elite functional fitness races, the atmosphere is designed to feel intense, high-density, and performance-driven.
* **Density & Mood**: High-contrast, dark mode, compact layout with warning/hazard accents. The interface avoids soft drop-shadows or "bubble" rounding, focusing instead on structural grids, sharp corners, and high-visibility indicators to evoke the feeling of a professional timing board.

## 2. Color Palette & Roles
* **Volt Neon Yellow (#e2ff3b)**: High-visibility primary accent color. Used for progress bars, top ranks, primary action buttons, and active headers.
* **Charcoal Asphalt Gray (#18181b)**: Main panel background. Simulates raw concrete/asphalt texture.
* **Deep Coal Black (#09090b)**: Main viewport background. Creates a high-contrast foundation for the Volt neon yellow data cards.
* **Hazard Amber (#f59e0b)**: Warning state color, used for connection alerts, timing countdowns, and medium-level status tags.
* **Crimson Rage Red (#f43f5e)**: Danger/offline state color, used for connection failures, heart rate warnings, and race stop actions.
* **Pure Performance White (#f8fafc)**: High-readability typography for primary numbers, speeds, and athlete names.

## 3. Typography Rules
* **Font Family**: Primary font is **Oswald** (for bold, high-density uppercase headings, rank numbers, and telemetry metrics) and **Outfit** (for body text and status descriptions).
* **Weights**:
  - Metric numbers & ranks: Heavy weight `800` (Condensed Bold).
  - Headings & Buttons: Bold weight `700` (Uppercase, letter-spacing: 0.1em).
  - Body & metadata: Light weight `400` / Semi-bold `600`.
* **Case and Spacing**: Global uppercase styling for all data labels and buttons to mimic timing boards.

## 4. Component Stylings
* **Buttons**:
  - **Primary**: Sharp-edged blocks (`border-radius: 4px`), filled with **Volt Neon Yellow (#e2ff3b)** with black text, turning into a slight glow on hover.
  - **Danger**: Filled with **Crimson Rage Red (#f43f5e)** with white text.
  - **Secondary**: Dark gray outline with light text.
* **Cards/Containers**:
  - Background is solid **Charcoal Asphalt Gray (#18181b)** with a 1px solid border of **#27272a** (Steel Border).
  - Sharp corners (`border-radius: 4px` or `rounded-sm`) to keep the industrial machine aesthetic.
  - No diffused drop-shadows; instead, uses a solid border color or an inner neon glow for highlighting active/top rank states.
* **Telemetry Data Cards**:
  - High-density grids showing large values in white next to tiny labels in **Volt Neon Yellow**.
  - Border highlights change color depending on connection status.

## 5. Layout Principles
* **Structure**: Grid-aligned elements with thick industrial borders (`border: 2px solid #27272a`).
* **Hazard Accents**: Diagonal striped hazard patterns (yellow/black) used as borders or header underlines to emphasize the "functional fitness zone" vibe.
* **Margins**: Tight, high-density padding (`p-4` or `p-6`) to display maximum telemetry values on a single screen without scrolling.

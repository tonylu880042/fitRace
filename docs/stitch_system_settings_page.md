# Google Stitch Prompt: FitRaceStudio System Settings

Use this prompt in Google Stitch to generate the FitRaceStudio system settings page.

## Design System

Create a high-density operations console for a local sports timing system. The atmosphere is industrial, precise, and competition-ready, like a race control booth for hardware operators. Density is 8/10, variance is 5/10, motion is 4/10. The UI must feel technical and reliable, not playful or marketing-led.

### Color Palette

- **Off Black Canvas** `#09090B` - full-page background.
- **Zinc Panel** `#18181B` - primary panel surface.
- **Deep Zinc Surface** `#111113` - inputs, secondary buttons, nested controls.
- **Zinc Border** `#27272A` - normal structural borders.
- **Strong Zinc Border** `#3F3F46` - active panel separators and button borders.
- **Race Lime** `#E2FF3B` - the only accent color, used for active states, primary actions, status highlights, and the brand wordmark.
- **Signal Green** `#34D399` - success status only.
- **Warning Amber** `#F59E0B` - validation warnings only.
- **Fault Red** `#F43F5E` - destructive or error state only.
- **Primary Text** `#FAFAFA` - primary labels and headings.
- **Muted Text** `#A1A1AA` - secondary descriptions, metadata, and helper text.

Never use purple, blue neon, multicolor gradients, pure black, glassmorphism, soft pastel cards, or glowing shadows. Do not introduce a second accent color.

### Typography

- **Display and section titles:** Oswald, uppercase, tight but readable letter spacing, strong weight.
- **Body and controls:** Outfit.
- **Traditional Chinese fallback:** Noto Sans TC.
- **Numbers and compact telemetry:** use tabular-looking numeric treatment; if a monospace font is available, use JetBrains Mono only for IDs and JSON previews.
- Avoid Inter, serif fonts, oversized hero typography, and marketing slogans.

### Layout Rules

- First screen is the working application, not a landing page.
- Use a max-width shell around `1480px`.
- Use a top operational header with brand, websocket status, hub version, and navigation actions.
- Use full-width sections and dense panels. Cards are allowed only for repeated rows, lane pairings, and validation summaries.
- Border radius must be `4px` or less.
- Use CSS grid for layout. No overlapping elements. No decorative blobs, orbs, or abstract illustrations.
- Desktop layout: summary metrics, then a full-width system settings panel, then operational panels.
- Tablet: collapse complex sections to one column.
- Mobile: every grid becomes one column, controls remain at least `44px` tall, no horizontal scroll.

### Component Behavior

- Buttons are square, tactile, uppercase, and compact.
- Primary button uses Race Lime background with Off Black text.
- Destructive buttons use Fault Red border and text, never filled by default.
- Inputs use label above input, fixed height around `42px`, dark fill, and 1px border.
- Validation messages render inline under the affected panel, not as toast-only feedback.
- JSON preview uses a dark code block with fixed height and scroll.
- Loading states should be skeleton rows matching the final layout, never circular spinners.

## Screen To Generate

Generate a page named **FitRaceStudio System Settings** for configuring the local hub and Hyrox lane/RFID setup.

### Header

At the top, create a dense control header:

- Title: `FitRaceStudio System Admin`
- Subtitle: `Edge node status, hardware mapping, Hyrox lane setup, and system maintenance`
- Status pills:
  - `Online` with small Race Lime dot
  - `Hub v--`
- Buttons:
  - `Dashboard`
  - `Game Admin`
  - `Signup`
  - `Maintenance Unlock`

### Summary Metrics

Directly below the header, show four compact metric blocks:

- `0/0` Edge Nodes
- `0` Streams
- `0` Stations
- `0/0` Ready Lanes

The metric number uses Oswald and Race Lime. Labels are uppercase muted text.

### Main System Settings Panel

Create a primary full-width panel titled **System Settings**. It contains three setting groups arranged in a dense asymmetric grid:

1. **Race Operation Mode**
   - Select: `Training`, `Competition`
   - Select: `Individual`, `Doubles`, `Relay`
   - Toggle row:
     - `Require explicit lane assignment`
     - `Reject lane mismatch reads`
     - `Allow diagnostic one-reader mode`

2. **Hyrox Lane Calibration**
   - Stage tabs:
     - `Sled Push`
     - `Sled Pull`
     - `Burpee Broad`
     - `Farmers Carry`
     - `Sandbag Lunges`
   - Inputs:
     - `Station Number`
     - `Lane Length (m)`
     - `Target Lengths`
     - `Target Distance (m)`
   - Computed readouts:
     - `Configured Distance`
     - `Round Trips`
     - `Ready Lanes`
   - Helper text:
     - `A length is one movement from one endpoint to the opposite endpoint. A round trip is two lengths.`

3. **RFID Lane Pairing**
   - Repeated lane cards. Each card contains:
     - `Lane ID`
     - `Lane Number`
     - `Start Node`
     - `Start Antenna`
     - `Finish Node`
     - `Finish Antenna`
   - Buttons:
     - `Add Lane`
     - `Validate Pairing`
     - `Save Draft`
     - `Copy JSON`
   - Validation summary:
     - Success: `All configured RFID read zones are uniquely paired.`
     - Error examples:
       - `Lane 1: start and finish must use different RFID read zones.`
       - `Sled Push lane 2: finish node and antenna are required.`

### Secondary Panels

Below the settings panel, include these operational panels:

1. **Edge Nodes**
   - Repeated rows with node name, online dot, IP or hostname, version, last seen, and stream chips.
   - Empty state: `No Edge Node heartbeat received.`

2. **Station Assignment**
   - Inputs:
     - `Station`
     - `Telemetry Stream`
     - `Signup Link`
   - Buttons:
     - `Assign Stream`
     - `Unassign Station`
     - `Copy Signup Link`
   - Repeated station rows showing station number, equipment type, assigned node, athlete, and signup URL.

3. **Updates**
   - Status pill: `never_checked`
   - Metadata row:
     - `Current`
     - `Latest Hub`
     - `Latest Edge`
     - `Signature`
   - Buttons:
     - `Check`
     - `Download`
     - `Install Hub`
     - `Apply Hub`

4. **System Power**
   - Status pill: `IDLE only`
   - Buttons:
     - `Restart Hub Service`
     - `Reboot Hub`
     - `Shutdown Hub`
     - `Shutdown System`

### JSON Preview

Include a JSON preview inside the Hyrox settings panel. It should show this shape:

```json
{
  "mode": "competition",
  "lane_stations": [
    {
      "station_number": 2,
      "station_stage": "sled_push",
      "lane_length_m": 12.5,
      "target_lengths": 4,
      "counting_rule": "alternate_start_finish_endpoints",
      "lanes": [
        {
          "lane_id": "lane-1",
          "lane_number": 1,
          "sensors": [
            { "endpoint": "start", "node_id": "rfid-reader-01", "antenna_id": "A1" },
            { "endpoint": "finish", "node_id": "rfid-reader-01", "antenna_id": "B1" }
          ]
        }
      ]
    }
  ]
}
```

### Motion

Use restrained operational motion only:

- Status dots can softly pulse through opacity.
- Active stage tab can animate border color and background opacity.
- Validation messages can fade in using opacity and translateY only.
- Do not animate layout dimensions.

### Anti-Patterns

Do not generate:

- A marketing landing page.
- A centered hero section.
- Rounded bubbly cards.
- Purple/blue gradients.
- Decorative abstract shapes.
- Emoji.
- 3 equal feature cards.
- Fake marketing copy.
- Large empty whitespace that reduces operational density.
- Any layout that hides the settings controls below the fold on desktop.

## Output Target

Generate the page as a production-ready responsive dashboard screen. Prefer semantic HTML and CSS classes that can be mapped back into the existing `hub_server/static/systemAdmin.html` implementation.

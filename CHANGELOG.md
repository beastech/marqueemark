# Changelog

This fork is based on MarqueeMark v1.3.4 by beastech. Version suffixes
identify the Electrocoin / Digital Marquee work in this repository.

## Unreleased — locked Neo Geo marquee release

- Fixed a crash loop when a saved display mode is loaded while HDMI is
  disconnected. Headless mode now retains display state and redraws it when
  HDMI becomes available.
- Restored tty1 as a local recovery path for installations affected by the
  earlier console-lockdown change, and stopped selecting a custom Plymouth
  theme during normal installation.
- Guarded the optional Plymouth artwork against shutdown mode and zero-size
  windows.
- Made the Neo Geo Plymouth theme an explicit, persistent installer opt-in;
  normal installs retain the stock Raspberry Pi OS boot presentation.
- Made the opted-in theme paint shutdown black before inspecting display
  geometry and quietly hand off the console while retaining tty1 recovery.
- Limited splash artwork to Plymouth's `boot` mode and blanked the framebuffer
  after Pygame exits, preventing its console restoration from flashing during
  reboot.
- Kept Raspberry Pi OS cloud-init boot output in journald for the opted-in
  cabinet presentation, preventing its saved tty1 buffer from resurfacing at
  shutdown while preserving the tty1 recovery login.
- Clear retained tty1 framebuffer text after both its recovery getty and
  MarqueeMark have settled, avoiding a race where agetty repainted its login
  banner after the original pre-start clear.
- Moved the opted-in cabinet presentation's local recovery login to tty2,
  because agetty repaints tty1 whenever SDL releases KMS. Default installs
  continue to use tty1; cabinet users retain recovery via Ctrl+Alt+F2.
- Play the native Neo Geo shutdown animation for real system reboots and
  power-offs, while skipping it for installer updates and ordinary service
  restarts. The sequence finishes on black before systemd stops the renderer.
- Avoid replaying that animation when the cabinet-loss countdown itself
  requests power-off; the completed countdown now hands directly to shutdown.
- Render the opted-in startup art through MarqueeMark after KMS is available,
  so a cold boot does not depend on Plymouth winning the early HDMI race.
- Draw that startup sequence procedurally through the same canvas proven by
  the shutdown animation, avoiding a static-image path that rendered black on
  the physical cold-start test.
- Keep Plymouth's early handoff black so it cannot duplicate the later native
  startup sequence on cold boot.
- Run the native startup sequence before saved-layout restoration, preventing
  the previous marquee from flashing before the boot animation.
- Resolve rotation from the selected display mode during construction, so a
  saved Ultrawide cold boot cannot inherit the Mini Marquee's 90-degree flag.
- Validate sudoers rules before installing them.

- Finalised the built-in Neo Geo 1 Slot, 2 Slot, 4 Slot, and 6 Slot artwork,
  all with consistent naming and stable library ordering. The active marquee
  remains highlighted in place rather than moving to the front of the list.
- Added a centred live “On display” preview that lists every assigned card,
  including the active NeoSD Pro game.
- Added a private exact-size mini-marquee quality cache. Generated variants
  now live in `cache/mini-marquees`, leaving shared game art untouched.
- Improved the Admin marquee-library flow: built-ins and saved custom
  marquees appear before the creation action, and an empty custom library has
  its own create call to action.

- Added an optional, configurable cabinet-safe Pi shutdown flow. When the
  NeoSD USB device disappears, MarqueeMark can show a Game Over sequence and
  cleanly halt the Pi after a user-selected 5–600 second delay.
- Added an explicit Advanced settings opt-in before cabinet-power controls
  become interactive. The feature is disabled by default and never treats
  normal game inactivity as a power-off signal.
- Replaced the experimental video shutdown art with a native reverse Neo Geo
  boot sequence: finished boot screen, mirrored logo, GAME OVER, then black.
  Added safe browser and on-marquee previews that never halt the Pi.

## v1.3.5-layout-modes.4 — Electrocoin full-height correction

- Removed the remaining black padding from the refreshed Electrocoin 4 Slot
  image so its lower border reaches the final row of the 1366 × 380 canvas.
- Updated its built-in card-window geometry for the corrected full-height art.

## v1.3.5-layout-modes.3 — Electrocoin canvas correction

- Corrected the refreshed Electrocoin 4 Slot artwork: its visible artwork now
  fills the complete **1366 × 380** canvas rather than leaving a black padded
  band at the bottom of the physical marquee.
- Adjusted the four built-in card windows to match the corrected artwork.

## v1.3.5-layout-modes.2 — Electrocoin four-slot refresh

- Replaced the provided **Electrocoin 4 Slot** Ultrawide template with a new
  1366 × 380 artwork treatment: four framed portrait-card windows, authentic
  Electrocoin lower labels and matching side speaker grilles.
- Kept the original legacy artwork file in place so existing saved layouts
  continue to render as they were configured.
- Removed the display-only NeoSD Pro cyan/red emphasis outline so physical
  HDMI output matches the Admin preview.

## v1.3.5-layout-modes.1 — Mini and Ultrawide Marquee modes

- Split the Admin experience into always-available **Mini Marquee** and
  **Ultrawide Marquee** areas. Browsing either area never changes the physical
  HDMI output.
- Added first-run display setup stored on the Pi, rather than in a browser:
  choose the connected Mini Marquee or Ultrawide Marquee and MarqueeMark uses
  that renderer after future service restarts and cold boots.
- Made later display-type changes deliberately safe: the user is warned that
  the output redraws immediately, then has 30 seconds to keep the change or
  automatically revert to the previous renderer.
- Added a clear **Monitor Output** status/control in the header and made the
  Mini/Ultrawide Admin tabs more prominent.
- Added built-in **Neo Geo 2 Slot**, **Neo Geo 4 Slot**, and **Neo Geo 6 Slot**
  Ultrawide templates, alongside the existing Electrocoin 4 Slot and Neo Geo
  1 Slot bases. Each maps the correct number of portrait mini-marquee windows.
- Updated the Ultrawide canvas and built-in assets to the measured
  **1366 × 380** visible panel area. The advanced viewport-height diagnostic
  remains available for unusual panels, but is kept separate from layouts and
  has no card assignments.
- Improved the Ultrawide workflow with a prominent **Create your own layout**
  action and an empty state for new installations with no live layout yet.
- Prevented the background status refresh from closing a card-art picker while
  it is being used. Saving assignments now provides a short **Saved** button
  acknowledgement without rebuilding the form.
- Refined the card-assignment controls with compact modern dark selectors,
  plus clearer template, preview, and output-status styling.
- Updated installation support so the required Ultrawide assets and current
  application code are included on fresh installs and updates.

## v1.3.4-electrocoin.19 — Layout library refinement

- Grouped provided cabinet designs under **Built-in templates** and saved
  designs under **Your layouts**.
- Standardised built-in names to the `Cabinet 4 Slot` / `Neo Geo 1 Slot`
  style and capitalised custom-editor slot counts.
- Replaced repeated “On display” text with a compact green dot beside the
  active layout, and changed the preview control to an eye icon.

## v1.3.4-electrocoin.18 — Gemini generation feedback

- Added progress feedback for slower Gemini image models and a three-minute
  timeout with a clear recovery message, so a stalled browser request cannot
  leave the generator permanently disabled.

## v1.3.4-electrocoin.17 — Layout labels and AI provider memory

- Expanded layout-pill labels to 50 characters before truncation; the full
  name remains available on hover.
- Remembered the last selected AI provider in the browser, independently of
  whether its API key is remembered on that device.

## v1.3.4-electrocoin.16 — Assigned-art previews

- The live Admin preview now shows a NeoSD Pro mini-marquee placeholder until
  the cabinet reports the current game.
- Saved-layout previews now render their saved card assignments instead of
  showing only cyan slot guides.

## v1.3.4-electrocoin.15 — Built-in template preview repair

- Fixed the Admin UI preview path for built-in layouts. The Neo Geo one-slot
  template now loads its installed artwork rather than showing a black
  fallback behind its slot guide.

## v1.3.4-electrocoin.14 — Built-in template installer repair

- Updated the installer to download missing built-in cabinet artwork on both
  fresh installs and updates, including the Neo Geo one-slot base image.
- Added a five-second service stop timeout to prevent stalled HDMI/SDL
  processes from holding updates for systemd's long default timeout.

## v1.3.4-electrocoin.13 — Headless Admin mode

- MarqueeMark now starts its Admin server without HDMI connected, using an
  off-screen surface when SDL/KMS cannot initialise a display.
- Reconnect HDMI and restart the service to resume physical marquee output.

## v1.3.4-electrocoin.12 — Neo Geo one-slot template

- Added a built-in Neo Geo one-slot marquee template at the native 1366 × 360
  canvas size.
- Mapped its black “Now Featuring” window as a single mini-marquee slot.
- Kept the new template out of the game-art selector and protected it from
  custom-layout editing/deletion controls.

## v1.3.4-electrocoin.11 — Uniform slot sizing

- Added **Keep all slots the same size** in the custom-layout editor.
- Choose a reference slot; enabling the option immediately matches every
  slot's dimensions to it while preserving each slot's position.
- While enabled, only the reference slot resizes all slots together; every
  slot remains independently movable.

## v1.3.4-electrocoin.10 — Live preview, model choice, assignment fix

- Fixed card assignments for an inactive selected layout being lost on the
  next Admin read. Saving assignments no longer changes the live display.
- Added a compact **On display** section with the actual live background and
  card-art preview, including NeoSD Pro updates when a new game is loaded.
- Added API-key validation and a provider-specific image-model picker before
  generation, so users can choose an available model such as Gemini's image
  models rather than relying on a fixed default.

## v1.3.4-electrocoin.9 — Gemini generation fix

- Corrected the Gemini image response format to JPEG, which its current
  image-generation endpoint accepts. MarqueeMark still normalises the result
  into its PNG background format.

## v1.3.4-electrocoin.8 — Browser-only AI backgrounds

- Added optional OpenAI and Google Gemini background generation inside the
  custom-layout editor.
- API keys are used directly by the browser and never sent to or stored on
  the Pi. They are session-only by default; **Remember this key on this
  device** is an explicit local-browser opt-in.
- Generated images are normalised through the existing 1366 × 360 background
  pipeline, then enter the normal slot-layout editor.
- Added a visible credit and link to raz0red's IFWG project, which inspired
  the browser-based BYOK workflow. No IFWG source code is included here.

## v1.3.4-electrocoin.7 — Safe display switching and colour layouts

- Separated the layout selected in Admin from the one currently on the
  physical marquee. Selecting, editing, saving, and assigning cards are now
  safe until **Send to display** is pressed explicitly.
- Added clear “On display” status, a live-layout badge, and a Send to display
  control that saves the selected layout's assignments before switching.
- Deleting the layout currently on the marquee now asks for confirmation and
  safely falls back to the built-in Electrocoin four-slot layout.
- Added solid-colour backgrounds for custom layouts, including a colour picker;
  an uploaded image is now optional.
- Disabled unavailable Save controls instead of leaving them green.

## v1.3.4-electrocoin.6 — Editing workflow polish

- Added a dedicated Rename action while editing a saved custom layout.
- Save changes now updates geometry/background directly and stays disabled
  until the layout has actually changed; it no longer opens the name dialog.
- Fixed URL decoding so layout names with spaces no longer display `+`.
- Truncated long layout pill labels after 15 characters while preserving the
  full name in the hover tooltip.

## v1.3.4-electrocoin.5 — Layout preview and editing

- Moved custom-layout naming into a Save dialog instead of showing a name
  field in the editor.
- Added compact Preview and Edit controls next to saved layout pills.
- Preview opens a modal with the background and non-interactive slot guides;
  it closes with the X button or a click outside the modal.
- Edit reopens a saved custom layout with its background and slot positions,
  and supports renaming it on save.

## v1.3.4-electrocoin.4 — Saved layout library

- Made the layout builder independent from card-marquee assignments.
- Custom layouts now require a name and save permanently as reusable layout
  pills containing only their background and slot positions.
- Added selection and deletion of saved custom layouts; deletion never removes
  game artwork.
- Preserved an existing pre-library Custom layout by importing it on upgrade.
- Stored card assignments separately for each layout, so changing layouts does
  not overwrite another layout's selections.
- Reduced inactive pill size and contrast so only the selected layout is
  visually highlighted.
- The installer now prints the exact installed MarqueeMark version.

## v1.3.4-electrocoin.3 — Custom-editor drag fix

- Kept the custom background visible while dragging or resizing a slot.
  The editor now moves only the slot overlay during a drag rather than
  rebuilding the canvas and reloading its background image every frame.

## v1.3.4-electrocoin.2 — Flexible Digital Marquee editor

- Renamed the configuration area to **Digital Marquee (1366 × 360)**.
- Added friendly game-title labels to the card-art selectors, with the PNG
  filename retained only when no title mapping is available.
- Simplified each card to one selector, with the live **NeoSD Pro** option
  clearly separated as a special option.
- Added selectable base-template pills.
- Added the **Custom** layout editor: upload a PNG/JPEG background, crop to
  fill or fit it to the marquee canvas, and position mini-marquee objects.
- Custom layouts support the real Neo Geo MVS slot counts: **1, 2, 4, or 6**.
  Slots can be moved and resized while retaining the correct proportions.
- Card assignments now adapt to the saved slot count.

## v1.3.4-electrocoin.1 — Electrocoin four-slot edition

- Added the wide 1366 × 360 Electrocoin display mode.
- Recreated the four-card Electrocoin presentation with a base image and
  overlaid mini-marquee windows.
- Added three fixed cartridge-art positions plus a highlighted live NeoSD Pro
  position, configurable from the Admin page.
- Added an installer path for this personal fork while preserving the
  Electrocoin service option across updates.

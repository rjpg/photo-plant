# Project Memory

## Objective

Build a local web server on a Raspberry Pi 4 Model B for a 4-camera plant monitoring system.

The system should:

- Use 4 cameras to photograph 4 plant trays continuously
- Allow capture interval configuration
- Provide a web UI on the Raspberry Pi itself
- Be usable from the local touchscreen and from a remote browser
- Allow per-camera setup/positioning
- Save captured images with a prefix defined in the UI and a sequential numeric suffix

Example filenames:

- `prefix_000001.jpg`
- `prefix_000002.jpg`
- `prefix_000003.jpg`

## Current Technical Direction

- Backend: `Python + Flask`
- Storage: local on Raspberry Pi
- UI: dashboard with latest image per camera
- Setup mode: one camera at a time
- Capture mode: sequential capture across camera ports

## Confirmed Hardware/Software State

- Raspberry Pi: `Raspberry Pi 4 Model B`
- Display: `Raspberry Pi Touch Display 2`
- Display works correctly when used alone
- SSH works
- OS: `Debian 12 (bookworm)` / Raspberry Pi OS 64-bit
- SSH user: `photoplant`
- SSH password: `photoplant`

## Arducam Findings

- Final board is a `4-port` Arducam board
- Sensor detected: `ov5647`
- The board behaves as a `camera mux`, not as 4 fully independent simultaneous camera feeds
- The correct software model is therefore:
  - one active camera at a time
  - sequential capture across 4 ports
  - latest-image dashboard for all 4 cameras

## Raspberry Camera Configuration Applied

`/boot/firmware/config.txt` was updated to:

- `camera_auto_detect=0`
- `dtoverlay=camera-mux-4port,cam0-ov5647,cam1-ov5647,cam2-ov5647,cam3-ov5647`

## Current Integration Constraint

- The display and Arducam HAT cannot currently coexist cleanly in the present physical mounting arrangement
- Likely issue is mechanical/GPIO stacking conflict rather than base software
- A GPIO stacking/extender solution was ordered:
  - `https://www.amazon.es/-/en/dp/B088DB57RY?smid=A187Y4UVM6ZA0X&ref_=chk_typ_imgToDp&th=1`

## Next Steps

1. Connect all 4 cameras
2. Reboot Raspberry Pi
3. Validate mux behaviour with all 4 connected
4. Identify practical camera-port switching method
5. Start Flask app scaffold
6. Implement sequential capture and file naming
7. Add web UI for:
   - prefix input
   - interval control
   - setup mode per camera
   - latest image per camera

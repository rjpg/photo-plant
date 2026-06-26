# Photo Plant

Local Flask web server for a Raspberry Pi 4 with a 4-port Arducam camera mux.

## Features

- Dashboard with latest image for each camera
- Per-camera prefix and interval configuration
- Manual capture per camera
- Preview capture per camera
- Sequential filename generation like `prefix_000001.jpg`

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Then open:

```text
http://<raspberry-ip>:5000
```

## Notes

- This project assumes the Raspberry Pi camera mux is already configured in `/boot/firmware/config.txt`
- Camera capture uses `rpicam-still --camera <index>`
- Captures are stored in `data/captures/cam0` through `data/captures/cam3`


<img width="1600" height="801" alt="WhatsApp Image 2026-05-12 at 02 16 00" src="https://github.com/user-attachments/assets/44aaa7f5-e7d6-422c-849a-dd67e2618b95" />

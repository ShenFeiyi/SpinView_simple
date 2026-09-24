# SpinView Simple

A native macOS camera-control app for the FLIR/Teledyne Blackfly S BFS-U3-51S5C.

## Launch

Connect the camera with a USB 3 data cable, close SpinView or any other app using
the camera, and double-click **Start SpinView.command**. The app connects
automatically with **30 fps** requested and **33 ms** exposure on every connection.
Alternatively, from this project directory:

```bash
.venv/bin/python app.py
```

The launcher uses this project's Python environment, so activating it manually
is unnecessary. Keep the launcher alongside the project files and `.venv`.

## Controls

- **Live color preview:** fits the full image without stretching or cropping.
  Acquisition runs in a separate thread. Preview updates are capped at 30 fps;
  the footer reports the measured rate at which frames are received.
- **Save image… / Command-S:** saves the frame shown when the button was pressed
  at full 2448 × 2048 resolution. TIFF (`.tif` or `.tiff`), PNG, and BMP preserve
  RGB8 pixels losslessly. JPEG uses quality 95. Snapshots are processed color
  images, not raw Bayer or 16-bit sensor data.
- **Exposure time:** entered in milliseconds. The camera's range is read live.
- **Gain:** entered in decibels.
- **Auto exposure / Auto gain:** click **Auto** beside the corresponding field
  to let the camera adjust that parameter once. The field shows the changing
  value while adjusting, then returns to manual control with the chosen value.
  The other parameter stays manual. It uses the camera's configured brightness
  target and automatic adjustment limits. If the scene cannot reach that target,
  adjustment stops after a timeout and keeps the latest value.
- **Frame rate:** sets the requested acquisition rate. Exposure and USB
  throughput may limit the actual rate. For example, a 300 ms exposure limits
  acquisition to approximately 3.3 fps even with a higher requested rate.
- **White balance:** adjust red and blue channel ratios manually, or use
  **Auto white balance once** with a well-lit neutral target. Auto adjustment
  temporarily locks the ratio controls and then returns them to manual mode.
- **Center crosshair:** toggles a cyan overlay at the center of the displayed
  image. It is never included in saved images.

The app uses manual exposure/gain and continuous, untriggered acquisition while
connected. It briefly pauses acquisition to apply setting changes and displays
the values read back from the camera. Original camera settings are restored
when disconnecting or quitting, when the camera remains accessible. It never
saves a persistent camera user set. Adjustments are therefore session-only.

On disconnect, the last frame remains available for saving and is labeled as
such. Use **Connect camera** to reconnect after an unplug or error.

## Environment

Validated on Apple Silicon with Python 3.12:

- Native Spinnaker SDK **4.4.0.246**
- Homebrew: `ffmpeg@6`, `libusb`, `libomp`
- Project `.venv`: PySpin **4.4.0.246**, NumPy **2.5.3**, PySide6 **6.11.2**

The PySpin wheel must match the native SDK version, Python ABI, and architecture.
The matching package on this Mac is:

```text
/Applications/Spinnaker/PySpin/spinnaker_python-4.4.0.246-cp312-cp312-macosx_14_0_arm64.tar.gz
```

No OpenCV, Matplotlib, or Pillow installation is required. `requirements.txt`
records the tested Python versions; install the vendor wheel before using it
to reproduce the environment.

### Set up a fresh checkout

First install Python 3.12 and the Apple Silicon Spinnaker 4.4.0.246 SDK from
[Teledyne](https://www.teledynevisionsolutions.com/products/spinnaker-sdk/).
Then, from the project directory:

```bash
brew install ffmpeg@6 libusb libomp
python3.12 -m venv .venv
mkdir -p .venv/vendor-wheel
tar -xzf /Applications/Spinnaker/PySpin/spinnaker_python-4.4.0.246-cp312-cp312-macosx_14_0_arm64.tar.gz \
  -C .venv/vendor-wheel \
  spinnaker_python-4.4.0.246-cp312-cp312-macosx_14_0_arm64.whl
.venv/bin/python -m pip install .venv/vendor-wheel/*.whl
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python app.py
```

The vendor archive contains the wheel and SDK documentation. The SDK, Python
wheel, and virtual environment are not included in this repository.

## Checks

```bash
.venv/bin/python check_camera.py
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

The readiness check enumerates cameras without changing settings. Tests cover
lossless image round trips, full-resolution saving, crosshair placement and
pixel isolation, UI control units/readback, shutdown, queued changes, and SDK
cleanup on failure.

Hardware validation on the connected BFS-U3-51S5C confirmed live RGB8 frames,
all five manual controls, approximately 20 fps at a 20 fps target with a suitable
exposure, successful TIFF/PNG snapshots, clean shutdown, and restoration of the
original settings. One-shot white balance was verified to start; convergence
depends on a suitably illuminated neutral target.

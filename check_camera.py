"""Check the Python environment and enumerate cameras without changing settings."""

import sys
from importlib.metadata import version


def main():
    try:
        import numpy
        import PySpin
        from PySide6 import QtCore
    except ImportError as error:
        print(f"Dependency check failed: {error}", file=sys.stderr)
        return 2

    print(f"Python: {sys.version.split()[0]}")
    print(f"PySpin: {version('spinnaker-python')}")
    print(f"NumPy: {numpy.__version__}")
    print(f"Qt: {QtCore.qVersion()}")

    try:
        system = PySpin.System.GetInstance()
        try:
            sdk = system.GetLibraryVersion()
            print(f"Spinnaker: {sdk.major}.{sdk.minor}.{sdk.type}.{sdk.build}")
            cameras = system.GetCameras()
            try:
                count = cameras.GetSize()
                print(f"Cameras detected: {count}")
                for index in range(count):
                    camera = cameras.GetByIndex(index)
                    try:
                        info = camera.TLDevice
                        try:
                            model = info.DeviceModelName.GetValue()
                            serial = info.DeviceSerialNumber.GetValue()
                            print(f"  {index}: {model} (serial {serial})")
                        finally:
                            del info
                    finally:
                        del camera
            finally:
                cameras.Clear()
        finally:
            system.ReleaseInstance()
    except PySpin.SpinnakerException as error:
        print(f"Camera discovery failed: {error}", file=sys.stderr)
        return 2

    if not count:
        print("Connect the camera using a USB 3 data cable, then run this check again.")
        return 1
    print("Camera discovery succeeded. Image acquisition has not been tested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

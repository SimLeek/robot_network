import os
import glob
import fcntl
import struct
from typing import List


class DeviceNotFoundError(Exception):
    """Raised when no valid, available hardware device can be found."""
    pass


def find_camera_devices() -> List[str]:
    """
    Finds available V4L2 video devices that actually support video capture
    and are not currently locked by another process.
    """
    valid_devices = []

    # Linux V4L2 Kernel Constants
    VIDIOC_QUERYCAP = 0x80685600
    V4L2_CAP_VIDEO_CAPTURE = 0x00000001
    V4L2_CAP_VIDEO_CAPTURE_MPLANE = 0x00001000

    for dev in sorted(glob.glob('/dev/video*')):
        try:
            # Attempt to open non-blocking. If another app has it locked, this fails.
            fd = os.open(dev, os.O_RDWR | os.O_NONBLOCK)
            try:
                # Issue ioctl to get device capabilities.
                # struct v4l2_capability is 104 bytes.
                cp = fcntl.ioctl(fd, VIDIOC_QUERYCAP, bytes(104))

                # Unpack the struct up to the 'capabilities' field (offset 80)
                # Format: 16s (driver), 32s (card), 32s (bus), I (version), I (capabilities)
                _, _, _, _, capabilities = struct.unpack('<16s32s32sII', cp[:88])

                # Check if it's an actual capture device, not a metadata/output node
                if capabilities & (V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_VIDEO_CAPTURE_MPLANE):
                    valid_devices.append(dev)
            finally:
                os.close(fd)
        except (OSError, IOError):
            # Device is busy (EBUSY), lacks permissions (EACCES), or disconnected
            continue

    return valid_devices


def get_first_camera_device() -> str:
    """Returns the first valid video device or raises an exception."""
    devices = find_camera_devices()
    if not devices:
        raise DeviceNotFoundError(
            "CRITICAL: No usable video capture devices found. "
            "They may be disconnected, locked by another process, or lack permissions."
        )
    return devices[0]


def find_mic_devices() -> List[str]:
    """
    Finds ALSA audio devices that explicitly support recording ('capture').
    Returns ALSA hardware strings (e.g., 'hw:0,0') and a safe 'default'.
    """
    valid_devices = []
    pcm_file = '/proc/asound/pcm'

    if not os.path.exists(pcm_file):
        return valid_devices

    try:
        with open(pcm_file, 'r') as f:
            for line in f:
                # Example line: "00-01: ALC892 Analog : ALC892 Analog : playback 1 : capture 1"
                if 'capture' in line:
                    parts = line.split(':')
                    if len(parts) >= 5 and 'capture' in parts[4]:
                        try:
                            # Verify capture channel count is > 0
                            capture_count = int(parts[4].split()[1])
                            if capture_count > 0:
                                # Extract card and device ID ("00-01" -> hw:0,1)
                                hw_id = parts[0].strip()
                                card, dev = hw_id.split('-')
                                valid_devices.append(f"hw:{int(card)},{int(dev)}")
                        except (ValueError, IndexError):
                            continue
    except IOError:
        pass

    # If physical capture hardware exists, Pipewire/PulseAudio 'default' is safe and preferred
    # for mixing/resampling, so we insert it at the top of the list.
    if valid_devices:
        valid_devices.insert(0, 'default')

    return valid_devices


def get_first_mic_device() -> str:
    """Returns the first valid audio capture device or raises an exception."""
    devices = find_mic_devices()
    if not devices:
        raise DeviceNotFoundError(
            "CRITICAL: No usable audio capture devices (microphones) found. "
            "Check hardware connections and permissions."
        )
    return devices[0]


def find_speaker_devices() -> List[str]:
    """
    Finds ALSA audio devices that explicitly support 'playback'.
    Returns ALSA hardware strings (e.g., 'hw:0,0') and prepends 'default'.
    """
    valid_devices = []
    pcm_file = '/proc/asound/pcm'

    if not os.path.exists(pcm_file):
        # If the sound subsystem is totally missing
        return []

    try:
        with open(pcm_file, 'r') as f:
            for line in f:
                # Example: "00-00: ALC892 Analog : ALC892 Analog : playback 1 : capture 1"
                if 'playback' in line:
                    parts = line.split(':')
                    # The playback info is usually the 4th segment (index 3)
                    # We look for 'playback' followed by a number > 0
                    for part in parts:
                        if 'playback' in part:
                            try:
                                count = int(part.strip().split()[1])
                                if count > 0:
                                    # Format the card-device ID (e.g., "00-00") to "hw:0,0"
                                    hw_id = parts[0].strip()
                                    card, dev = hw_id.split('-')
                                    valid_devices.append(f"hw:{int(card)},{int(dev)}")
                                    break
                            except (ValueError, IndexError):
                                continue
    except IOError:
        pass

    # Prepend 'default' if we found hardware, as it's the most compatible GStreamer sink
    if valid_devices:
        valid_devices.insert(0, 'default')

    return valid_devices


def get_first_speaker_device() -> str:
    """Returns the first valid audio playback device or raises an exception."""
    devices = find_speaker_devices()
    if not devices:
        raise DeviceNotFoundError(
            "CRITICAL: No audio output (playback) devices found. "
            "Ensure sound cards are initialized in /proc/asound/cards."
        )
    return devices[0]

# --- Usage ---
if __name__ == "__main__":
    # This will immediately blow up if hardware is missing or broken.
    camera = get_first_camera_device()
    mic = get_first_mic_device()
    speaker = get_first_speaker_device()

    print(f"Available Cameras: {find_camera_devices()}")
    print(f"Available Mics: {find_mic_devices()}")
    print(f"Available Speakers: {find_speaker_devices()}")

    print(f"-> Selected: {camera} | {mic} | {speaker}")

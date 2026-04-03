"""
robot.py — Unified interface for the MasterPi telepresence robot.

Single entry point for all robot I/O:

  Outputs (you → robot):
    robot.drive(MoveCommand)          — wheels
    robot.arm_set_velocity(ArmVelocity) — arm joints (60 Hz velocity control)
    robot.arm_set_pose(ArmPose)       — arm joints (absolute, with duration)
    robot.arm_stop() / arm_release()  — halt / de-energize arm
    robot.buzzer(freq, on, off, reps) — buzzer
    robot.speak(chunk: np.ndarray)    — push float32 audio to speaker

  Inputs (robot → you, via callbacks set before calling start()):
    robot.on_frame    = fn(np.ndarray)  — camera RGB24 frames (H, W, 3) uint8
    robot.on_audio    = fn(np.ndarray)  — mic chunks, float32 [-1, 1]

  Polled state (always up to date):
    robot.arm_get_pose() → ArmPose
    robot.arm_get_hw_staleness() → Dict[int, float]
    robot.battery_v  → float | None
    robot.battery_mv → int | None

Usage:
    robot = Robot()
    robot.on_frame = lambda f: ...
    robot.on_audio = lambda a: ...
    robot.start()

    # in your 60 Hz loop:
    robot.arm_set_velocity(ArmVelocity(grip=1.0))
    robot.arm_tick()          # or arm_tick_blocking() to own the timing

    robot.stop()

Dependencies:
  pip install git+https://github.com/simleek/PyV4L2Cam.git
  apt-get install libv4l-dev
"""

import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np

from examples.basicpibot.masterpi_core import Board, PacketFunction

# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------
TICK_RATE: float = 60.0
DT: float = 1.0 / TICK_RATE

# ---------------------------------------------------------------------------
# Arm config
# ---------------------------------------------------------------------------
ARM_MAX_SPEED: float = 1.5

ARM_JOINT_MAX_SPEED: Dict[int, float] = {
    1: 1.0,   # grip
    3: 1.5,   # p3
    4: 1.5,   # p2
    5: 1.5,   # p1
    6: 1.0,   # yaw
}

ARM_SERVO_IDS = [1, 3, 4, 5, 6]

ARM_JOINT_LIMITS: Dict[int, tuple] = {
    1: (0.0,   1.0),    # grip:  0=closed, 1=open
    3: (-1.0,  0.25),   # p3:   avoid camera cable squish
    4: (-0.75, 1.0),    # p2:   avoid self-intersection
    5: (-0.25, 1.0),    # p1:   avoid self-intersection with back
    6: (-1.0,  1.0),    # yaw:  full range
}

# ===========================================================================
# Helpers
# ===========================================================================

def clamp(val: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(min(float(val), hi), lo)

def normalized_to_pulse(val: float) -> int:
    return int(1500 + clamp(val) * 1000)

def pulse_to_normalized(pulse: int) -> float:
    return clamp((pulse - 1500) / 1000.0)


# ===========================================================================
# Data classes
# ===========================================================================

@dataclass(frozen=True)
class MotorConfig:
    """Low-level duty cycles for the 4 individual motors."""
    m1: float = 0.0
    m2: float = 0.0
    m3: float = 0.0
    m4: float = 0.0

    def to_legacy_list(self) -> List[List]:
        return [
            [1,  self.m1*100.0],
            [2,  self.m2*100.0],
            [3, -self.m3*100.0],
            [4, -self.m4*100.0],
        ]


@dataclass(frozen=True)
class MoveCommand:
    """High-level 4DOF movement vector, normalized [-1, 1]."""
    fwd: float = 0.0
    strafe_right: float = 0.0
    turn_right: float = 0.0
    in_out: float = 0.0

    def to_motor_config(self) -> MotorConfig:
        m1 = self.fwd - self.strafe_right - self.turn_right + self.in_out
        m2 = self.fwd + self.strafe_right - self.turn_right - self.in_out
        m3 = self.fwd + self.strafe_right + self.turn_right + self.in_out
        m4 = self.fwd - self.strafe_right + self.turn_right - self.in_out
        return MotorConfig(m1=m1, m2=m2, m3=m3, m4=m4)


@dataclass(frozen=True)
class ArmPose:
    """Absolute arm position, normalized [-1, 1]. None = don't touch that joint."""
    yaw:  Optional[float] = None   # servo 6
    p1:   Optional[float] = None   # servo 5
    p2:   Optional[float] = None   # servo 4
    p3:   Optional[float] = None   # servo 3
    grip: Optional[float] = None   # servo 1

    _MAPPING = {6: 'yaw', 5: 'p1', 4: 'p2', 3: 'p3', 1: 'grip'}

    def to_legacy_list(self) -> List[List[int]]:
        result = []
        for sid, attr in self._MAPPING.items():
            val = getattr(self, attr)
            if val is not None:
                result.append([sid, normalized_to_pulse(val)])
        return result

    @classmethod
    def from_servo_dict(cls, d: Dict[int, float]) -> 'ArmPose':
        kwargs = {}
        for sid, attr in cls._MAPPING.items():
            if sid in d:
                kwargs[attr] = d[sid]
        return cls(**kwargs)


@dataclass(frozen=True)
class ArmVelocity:
    """Desired joint velocity, normalized [-1, 1]. None = don't update that joint."""
    yaw:  Optional[float] = None
    p1:   Optional[float] = None
    p2:   Optional[float] = None
    p3:   Optional[float] = None
    grip: Optional[float] = None

    _MAPPING = {6: 'yaw', 5: 'p1', 4: 'p2', 3: 'p3', 1: 'grip'}

    def to_servo_dict(self) -> Dict[int, float]:
        return {
            sid: getattr(self, attr)
            for sid, attr in self._MAPPING.items()
            if getattr(self, attr) is not None
        }


# ===========================================================================
# Robot
# ===========================================================================

class Robot:
    """
    Unified interface for the MasterPi robot.
    Set callbacks, then call start(). Call stop() on shutdown.
    """

    # Assign these before calling start():
    on_frame: Optional[Callable[[np.ndarray, int, int, str], None]] = None  # (H,W,3) uint8 RGB
    on_audio: Optional[Callable[[np.ndarray], None]] = None  # float32 [-1,1] chunk

    _MIN_MOVE_DURATION = 0.04  # seconds — below this the hw interpolator misbehaves
    _STOP_DURATION = 0.0  # 0 ms = "hold here immediately"
    _DEAD_BAND = 0.002  # normalized — don't re-issue if velocity barely changed

    FRAME_TYPE = "raw"  # raw or array

    def __init__(self,
                 serial_device: str = '/dev/ttyS0',
                 baudrate: int = 1_000_000,
                 max_buzzer_time: float = 2.0):

        self.max_buzzer_time   = max_buzzer_time

        # --- Board ---
        self._board = Board(device=serial_device, baudrate=baudrate)
        self._board.enable_reception()

        # --- Arm state ---
        self._pos_lock = threading.Lock()
        self._arm_pos: Dict[int, float] = self._read_arm_pos_blocking()
        self._arm_vel: Dict[int, float] = {sid: 0.0 for sid in ARM_SERVO_IDS}
        self._active_vel: Dict[int, float] = {sid: 0.0 for sid in ARM_SERVO_IDS}
        self._move_deadline: Dict[int, float] = {sid: 0.0 for sid in ARM_SERVO_IDS}
        self._last_tick = time.monotonic()
        self._hw_last_read: Dict[int, float] = {sid: time.monotonic() for sid in ARM_SERVO_IDS}

        # --- Battery ---
        self._battery_mv: Optional[int] = None

        # Inject serial callbacks
        self._board.parsers[PacketFunction.PACKET_FUNC_PWM_SERVO] = self._on_pwm_servo_packet
        self._board.parsers[PacketFunction.PACKET_FUNC_SYS]       = self._on_sys_packet

        # Background threads (started in start())
        self._running     = False
        self._threads: List[threading.Thread] = []

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self):
        """Start all background I/O threads."""
        self._running = True

        # Arm position reader
        self._threads.append(threading.Thread(
            target=self._hw_reader_loop, daemon=True, name='arm-hw-reader'))

        for t in self._threads:
            t.start()

    def stop(self):
        """Stop all threads and silence the robot."""
        self._running = False
        self.arm_stop()
        self.stop_drive()

    # -----------------------------------------------------------------------
    # Serial packet callbacks (called from Board.recv_task thread)
    # -----------------------------------------------------------------------

    def _on_sys_packet(self, data: bytes):
        try:
            if data[0] == 0x04:
                self._battery_mv = struct.unpack('<H', data[1:])[0]
        except Exception:
            pass

    def _on_pwm_servo_packet(self, data: bytes):
        try:
            sid, cmd, info = struct.unpack("<BBH", data)
            if cmd == 0x05 and sid in self._arm_pos:
                corrected = pulse_to_normalized(info)
                with self._pos_lock:
                    self._arm_pos[sid] = corrected
                    self._hw_last_read[sid] = time.monotonic()
            else:
                try:
                    self._board.pwm_servo_queue.put_nowait(data)
                except Exception:
                    pass
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Background loops
    # -----------------------------------------------------------------------

    def _hw_reader_loop(self):
        """Round-robin servo position read requests. Responses handled async."""
        sids = list(ARM_SERVO_IDS)
        idx  = 0
        while self._running:
            sid = sids[idx % len(sids)]
            idx += 1
            try:
                self._board.buf_write(PacketFunction.PACKET_FUNC_PWM_SERVO, [0x05, sid])
            except Exception:
                pass
            time.sleep(1.0 / (TICK_RATE * len(sids)))

    # -----------------------------------------------------------------------
    # Public API — outputs
    # -----------------------------------------------------------------------

    def drive(self, cmd: MoveCommand):
        """Set wheel velocities from a MoveCommand."""
        self._board.set_motor_duty(cmd.to_motor_config().to_legacy_list())

    def stop_drive(self):
        self.drive(MoveCommand())

    def buzzer(self, freq: int, on_t: float, off_t: float, reps: int):
        """Trigger buzzer, clamped to max_buzzer_time total."""
        total = (on_t + off_t) * reps
        if total > self.max_buzzer_time:
            cycle = on_t + off_t
            if cycle > 0:
                reps = int(self.max_buzzer_time // cycle)
                rem  = self.max_buzzer_time - reps * cycle
                if rem > 0:
                    reps += 1
                    if rem < on_t:
                        on_t, off_t = rem, 0.0
                    else:
                        off_t = rem - on_t
            if reps == 0:
                reps, on_t, off_t = 1, min(on_t, self.max_buzzer_time), 0.0
        self._board.set_buzzer(freq, on_t, off_t, reps)

    def arm_set_velocity(self, vel: ArmVelocity):
        """Set desired velocity for one or more joints."""
        for sid, v in vel.to_servo_dict().items():
            self._arm_vel[sid] = clamp(v)

    def arm_tick(self, dt: Optional[float] = None) -> ArmPose:
        now = time.monotonic()
        if dt is None:
            dt = now - self._last_tick
        self._last_tick = now

        with self._pos_lock:
            pos_snapshot = dict(self._arm_pos)

        for sid in ARM_SERVO_IDS:
            vel      = self._arm_vel[sid]
            prev_vel = self._active_vel[sid]
            in_flight = self._move_deadline[sid] > now

            vel_changed = abs(vel - prev_vel) > self._DEAD_BAND

            if not vel_changed and in_flight:
                continue   # this joint is mid-move with unchanged intent — leave it alone

            # --- intent changed or last move expired ---
            self._active_vel[sid] = vel
            lo, hi = ARM_JOINT_LIMITS.get(sid, (-1.0, 1.0))
            cur    = pos_snapshot[sid]

            if vel == 0.0:
                # Snap-stop: command current position, near-zero duration.
                # This overwrites any in-flight move on the hardware side.
                pulse = normalized_to_pulse(clamp(cur, lo, hi))
                self._board.pwm_servo_set_position(self._STOP_DURATION, [[sid, pulse]])
                self._move_deadline[sid] = 0.0
                continue

            # Move toward the appropriate limit
            target   = hi if vel > 0 else lo
            distance = abs(target - cur)
            if distance < 0.001:
                continue   # already at (or past) the limit

            max_speed = ARM_JOINT_MAX_SPEED.get(sid, ARM_MAX_SPEED)
            duration  = max(distance / (abs(vel) * max_speed), self._MIN_MOVE_DURATION)

            self._board.pwm_servo_set_position(duration, [[sid, normalized_to_pulse(target)]])
            self._move_deadline[sid] = now + duration

        with self._pos_lock:
            return ArmPose.from_servo_dict(dict(self._arm_pos))

    def arm_tick_blocking(self) -> ArmPose:
        """Sleep to next 60 Hz boundary, then tick."""
        remaining = DT - (time.monotonic() - self._last_tick)
        if remaining > 0:
            time.sleep(remaining)
        return self.arm_tick()

    def arm_set_pose(self, pose: ArmPose, duration: float = 0.5):
        """Command an absolute pose. Updates internal state."""
        commands = pose.to_legacy_list()
        if commands:
            self._board.pwm_servo_set_position(duration, commands)
            with self._pos_lock:
                for sid, pulse in ((c[0], c[1]) for c in commands):
                    lo, hi = ARM_JOINT_LIMITS.get(sid, (-1.0, 1.0))
                    self._arm_pos[sid] = clamp(pulse_to_normalized(pulse), lo, hi)

    def arm_stop(self):
        """Zero all joint velocities."""
        self._arm_vel = {sid: 0.0 for sid in ARM_SERVO_IDS}

    def arm_release(self):
        """Cut power to all arm servos (go limp)."""
        self.arm_stop()
        self._board.pwm_servo_set_position(0.0, [[sid, 0] for sid in ARM_SERVO_IDS])

    # -----------------------------------------------------------------------
    # Public API — inputs / state
    # -----------------------------------------------------------------------

    def arm_get_pose(self) -> ArmPose:
        """Current arm pose from hw-corrected software state."""
        with self._pos_lock:
            return ArmPose.from_servo_dict(dict(self._arm_pos))

    def arm_get_hw_staleness(self) -> Dict[int, float]:
        """Seconds since last successful hardware read per servo."""
        now = time.monotonic()
        return {sid: now - t for sid, t in self._hw_last_read.items()}

    @property
    def battery_mv(self) -> Optional[int]:
        return self._battery_mv

    @property
    def battery_v(self) -> Optional[float]:
        return self._battery_mv / 1000.0 if self._battery_mv is not None else None

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _read_arm_pos_blocking(self) -> Dict[int, float]:
        pos = {}
        for sid in ARM_SERVO_IDS:
            try:
                pulse = self._board.pwm_servo_read_position(sid)
                pos[sid] = pulse_to_normalized(pulse) if pulse else 0.0
            except Exception:
                pos[sid] = 0.0
        return pos


# ===========================================================================
# Example
# ===========================================================================

if __name__ == '__main__':

    robot = Robot()

    robot.on_frame = lambda f: None   # drop frames for this test

    robot.start()
    print(f"Battery: {robot.battery_v} V")
    print(f"Arm pose: {robot.arm_get_pose()}")

    try:
        print("Sweeping grip open for 2s...")
        for _ in range(int(TICK_RATE * 2)):
            robot.arm_set_velocity(ArmVelocity(grip=-0.1))
            robot.arm_tick_blocking()
        robot.arm_stop()
        print(f"Final pose: {robot.arm_get_pose()}")
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        robot.stop()
        print("Done.")

import unittest
import numpy as np
from robonet.buffers.buffer_handling import pack_obj, unpack_obj
from robonet.buffers.buffer_objects import WifiSetupInfo, CVCamFrame, AudioBuffer, HumidityWaterBuffer, \
    TemperatureMonitorBuffer, IMUBuffer, TensorBuffer, MouseEvent, ReadSelectionRequest, SelectionText


class TestBufferObjects(unittest.TestCase):

    def test_wifi_setup_info(self):
        original = WifiSetupInfo(ssid="TestNetwork", server_ip="192.168.1.1", client_ip="192.168.1.2")
        packed = pack_obj(original)
        unpacked = unpack_obj(packed)

        self.assertEqual(original.ssid, unpacked.ssid)
        self.assertEqual(original.server_ip, unpacked.server_ip)
        self.assertEqual(original.client_ip, unpacked.client_ip)
        self.assertEqual(original.password, unpacked.password)

    def test_cam_frame(self):
        image = np.random.randint(0, 256, size=(480, 640, 3), dtype=np.uint8)
        original = CVCamFrame(cv_image=image, brightness=50, exposure=100)
        packed = pack_obj(original)
        unpacked = unpack_obj(packed)

        np.testing.assert_array_equal(original.cv_image, unpacked.cv_image)
        self.assertEqual(original.brightness, unpacked.brightness)
        self.assertEqual(original.exposure, unpacked.exposure)

    def test_audio_buffer(self):
        audio_data = [np.random.rand(1000).astype(np.complex128) for _ in range(2)]  # Stereo audio
        original = AudioBuffer(fft_data=audio_data, sample_rate=44100)
        packed = pack_obj(original)
        unpacked = unpack_obj(packed)

        self.assertEqual(len(original.fft_data), len(unpacked.fft_data))
        for orig_channel, unpacked_channel in zip(original.fft_data, unpacked.fft_data):
            np.testing.assert_array_almost_equal(orig_channel, unpacked_channel)
        self.assertEqual(original.sample_rate, unpacked.sample_rate)

    def test_tensor_buffer(self):
        audio_data = [np.random.rand(1000).astype(np.float32).reshape([10, 10, 10]) for _ in range(2)]  # Stereo audio
        original = TensorBuffer(tensors=audio_data)
        packed = pack_obj(original)
        unpacked = unpack_obj(packed)

        self.assertEqual(len(original.tensors), len(unpacked.tensors))
        for orig_channel, unpacked_channel in zip(original.tensors, unpacked.tensors):
            np.testing.assert_array_almost_equal(orig_channel, unpacked_channel)

    def test_humidity_water_buffer(self):
        original = HumidityWaterBuffer(humidity=65.5, water_detected=True)
        packed = pack_obj(original)
        unpacked = unpack_obj(packed)

        self.assertAlmostEqual(original.humidity, unpacked.humidity)
        self.assertEqual(original.water_detected, unpacked.water_detected)

    def test_temperature_monitor_buffer(self):
        original = TemperatureMonitorBuffer(temperature_readings=[20.5, 22.3, 21.8, 23.1])
        packed = pack_obj(original)
        unpacked = unpack_obj(packed)

        np.testing.assert_almost_equal(original.temperature_readings, unpacked.temperature_readings, 5)

    def test_imu_buffer(self):
        original = IMUBuffer(
            accel_data=(1.0, -0.5, 9.8),
            gyro_data=(0.1, 0.2, -0.1),
            mag_data=(25.0, 30.0, 40.0)
        )
        packed = pack_obj(original)
        unpacked = unpack_obj(packed)

        np.testing.assert_almost_equal(original.accel_data, unpacked.accel_data, 5)
        np.testing.assert_almost_equal(original.gyro_data, unpacked.gyro_data, 5)
        np.testing.assert_almost_equal(original.mag_data, unpacked.mag_data, 5)

    def test_imu_buffer_with_none(self):
        original = IMUBuffer(accel_data=(1.0, -0.5, 9.8), gyro_data=None, mag_data=(25.0, 30.0, 40.0))
        packed = pack_obj(original)
        unpacked = unpack_obj(packed)

        np.testing.assert_almost_equal(original.accel_data, unpacked.accel_data, 5)
        self.assertIsNone(unpacked.gyro_data)
        np.testing.assert_almost_equal(original.mag_data, unpacked.mag_data, 5)


class TestMouseEventDelta(unittest.TestCase):
    """Regression tests: delta can legitimately be negative (scrolling
    down), but MouseEvent originally used _uint32 for every field
    since all five are annotated plain `int` -- pack_obj's dispatch is
    by annotation identity (type_list.index(annotation)), not field
    position, so distinguishing delta needed a distinct marker type
    (SInt32), not just changing field_codecs[4]."""

    def test_negative_delta_round_trips(self):
        original = MouseEvent(event_type=3, x=0, y=0, button=0, delta=-5)
        packed = pack_obj(original)
        unpacked = unpack_obj(packed)
        self.assertEqual(unpacked.delta, -5)

    def test_positive_delta_round_trips(self):
        original = MouseEvent(event_type=3, x=0, y=0, button=0, delta=5)
        packed = pack_obj(original)
        unpacked = unpack_obj(packed)
        self.assertEqual(unpacked.delta, 5)

    def test_zero_delta_round_trips(self):
        original = MouseEvent(event_type=3, x=0, y=0, button=0, delta=0)
        packed = pack_obj(original)
        unpacked = unpack_obj(packed)
        self.assertEqual(unpacked.delta, 0)

    def test_x_y_button_event_type_still_use_unsigned(self):
        # The other four fields are legitimately non-negative (screen
        # coordinates, enum-like values) -- confirm the marker-type fix
        # for delta didn't accidentally change these too.
        original = MouseEvent(event_type=1, x=1920, y=1080, button=2, delta=0)
        packed = pack_obj(original)
        unpacked = unpack_obj(packed)
        self.assertEqual((unpacked.event_type, unpacked.x, unpacked.y, unpacked.button), (1, 1920, 1080, 2))


if __name__ == '__main__':
    unittest.main()


class TestSelectionMessages(unittest.TestCase):

    def test_read_selection_request_round_trip_default(self):
        original = ReadSelectionRequest()
        unpacked = unpack_obj(pack_obj(original))
        self.assertEqual(unpacked.max_chars, 5120)

    def test_read_selection_request_round_trip_custom(self):
        original = ReadSelectionRequest(max_chars=250)
        unpacked = unpack_obj(pack_obj(original))
        self.assertEqual(unpacked.max_chars, 250)

    def test_selection_text_round_trip(self):
        original = SelectionText(text='some highlighted text')
        unpacked = unpack_obj(pack_obj(original))
        self.assertEqual(unpacked.text, 'some highlighted text')

    def test_selection_text_round_trip_with_the_placeholder(self):
        original = SelectionText(text='no text highlighted')
        unpacked = unpack_obj(pack_obj(original))
        self.assertEqual(unpacked.text, 'no text highlighted')

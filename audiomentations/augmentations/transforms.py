import functools
import random
from copy import deepcopy
from pathlib import Path

import librosa
import numpy as np
from scipy.signal import butter, lfilter, convolve

from audiomentations.core.transforms_interface import BasicTransform
from audiomentations.core.utils import (
    read_dir,
    calculate_rms,
    calculate_desired_noise_rms,
)


class AddImpulseResponse(BasicTransform):
    """Convolve the audio with a random impulse response.
    Impulse responses can be created using http://tulrich.com/recording/ir_capture/
    Impulse responses are represented as wav files in the given ir_path.
    """

    def __init__(self, ir_path="/tmp/ir", p=0.5):
        """
        :param ir_path: Path to a folder that contains one or more wav files of impulse
        responses. Must be str or a Path instance.
        :param p:
        """
        super().__init__(p)
        self.ir_files = read_dir(ir_path)
        self.ir_files = [
            p
            for p in self.ir_files
            if Path(p).suffix.lower() in {".flac", ".mp3", ".ogg", ".wav"}
        ]
        assert len(self.ir_files) > 0

    @staticmethod
    @functools.lru_cache(maxsize=128)
    def __load_ir(file_path, sample_rate):
        return librosa.load(file_path, sample_rate)

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            self.parameters["ir_file_path"] = random.choice(self.ir_files)

    def apply(self, samples, sample_rate):
        ir, sample_rate2 = self.__load_ir(self.parameters["ir_file_path"], sample_rate)
        if sample_rate != sample_rate2:
            # This will typically not happen, as librosa should automatically resample the
            # impulse response sound to the desired sample rate
            raise Exception(
                "Recording sample rate {} did not match Impulse Response signal"
                " sample rate {}!".format(sample_rate, sample_rate2)
            )
        signal_ir = convolve(samples, ir)
        max_value = max(np.amax(signal_ir), -np.amin(signal_ir))
        scale = 0.5 / max_value
        signal_ir *= scale

        return signal_ir


class FrequencyMask(BasicTransform):
    """
    Mask some frequency band on the spectrogram.
    Inspired by https://arxiv.org/pdf/1904.08779.pdf
    """

    def __init__(self, min_frequency_band=0.0, max_frequency_band=0.5, p=0.5):
        """
        :param min_frequency_band: Minimum bandwidth, float
        :param max_frequency_band: Maximum bandwidth, float
        :param p:
        """
        super().__init__(p)
        self.min_frequency_band = min_frequency_band
        self.max_frequency_band = max_frequency_band

    def __butter_bandstop(self, lowcut, highcut, fs, order=5):
        nyq = 0.5 * fs
        low = lowcut / nyq
        high = highcut / nyq
        b, a = butter(order, [low, high], btype="bandstop")
        return b, a

    def __butter_bandstop_filter(self, data, lowcut, highcut, fs, order=5):
        b, a = self.__butter_bandstop(lowcut, highcut, fs, order=order)
        y = lfilter(b, a, data).astype(np.float32)
        return y

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            self.parameters["bandwidth"] = random.randint(
                self.min_frequency_band * sample_rate // 2,
                self.max_frequency_band * sample_rate // 2,
            )
            self.parameters["freq_start"] = random.randint(
                16, sample_rate / 2 - self.parameters["bandwidth"]
            )

    def apply(self, samples, sample_rate):
        bandwidth = self.parameters["bandwidth"]
        freq_start = self.parameters["freq_start"]
        samples = self.__butter_bandstop_filter(
            samples, freq_start, freq_start + bandwidth, sample_rate, order=6
        )
        return samples


class TimeMask(BasicTransform):
    """
    Mask some time band on the spectrogram.
    Inspired by https://arxiv.org/pdf/1904.08779.pdf
    """

    def __init__(self, min_band_part=0.0, max_band_part=0.5, fade=False, p=0.5):
        """
        :param min_band_part: Minimum length of the silent part as a fraction of the
            total sound length. Float.
        :param max_band_part: Maximum length of the silent part as a fraction of the
            total sound length. Float.
        :param fade: Bool, Add linear fade in and fade out of the silent part.
        :param p:
        """
        super().__init__(p)
        self.min_band_part = min_band_part
        self.max_band_part = max_band_part
        self.fade = fade

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            num_samples = samples.shape[0]
            self.parameters["t"] = random.randint(
                int(num_samples * self.min_band_part),
                int(num_samples * self.max_band_part),
            )
            self.parameters["t0"] = random.randint(
                0, num_samples - self.parameters["t"]
            )

    def apply(self, samples, sample_rate):
        new_samples = samples.copy()
        t = self.parameters["t"]
        t0 = self.parameters["t0"]
        mask = np.zeros(t)
        if self.fade:
            fade_length = min(int(sample_rate * 0.01), int(t * 0.1))
            mask[0:fade_length] = np.linspace(1, 0, num=fade_length)
            mask[-fade_length:] = np.linspace(0, 1, num=fade_length)
        new_samples[t0 : t0 + t] *= mask
        return new_samples


class AddGaussianSNR(BasicTransform):
    """Add gaussian noise to the samples with random Signal to Noise Ratio (SNR)"""

    def __init__(self, min_SNR=0.001, max_SNR=1.0, p=0.5):
        """
        :param min_SNR: Minimum signal-to-noise ratio
        :param max_SNR: Maximum signal-to-noise ratio
        :param p:
        """
        super().__init__(p)
        self.min_SNR = min_SNR
        self.max_SNR = max_SNR

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            std = np.std(samples)
            self.parameters["noise_std"] = random.uniform(
                self.min_SNR * std, self.max_SNR * std
            )

    def apply(self, samples, sample_rate):
        noise = np.random.normal(
            0.0, self.parameters["noise_std"], size=len(samples)
        ).astype(np.float32)
        return samples + noise


class AddGaussianNoise(BasicTransform):
    """Add gaussian noise to the samples"""

    def __init__(self, min_amplitude=0.001, max_amplitude=0.015, p=0.5):
        super().__init__(p)
        self.min_amplitude = min_amplitude
        self.max_amplitude = max_amplitude

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            self.parameters["amplitude"] = random.uniform(
                self.min_amplitude, self.max_amplitude
            )

    def apply(self, samples, sample_rate):
        noise = np.random.randn(len(samples)).astype(np.float32)
        samples = samples + self.parameters["amplitude"] * noise
        return samples


class TimeStretch(BasicTransform):
    """Time stretch the signal without changing the pitch"""

    def __init__(self, min_rate=0.8, max_rate=1.25, leave_length_unchanged=True, p=0.5):
        super().__init__(p)
        assert min_rate > 0.1
        assert max_rate < 10
        assert min_rate <= max_rate
        self.min_rate = min_rate
        self.max_rate = max_rate
        self.leave_length_unchanged = leave_length_unchanged

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            """
            If rate > 1, then the signal is sped up.
            If rate < 1, then the signal is slowed down.
            """
            self.parameters["rate"] = random.uniform(self.min_rate, self.max_rate)

    def apply(self, samples, sample_rate):
        time_stretched_samples = librosa.effects.time_stretch(
            samples, self.parameters["rate"]
        )
        if self.leave_length_unchanged:
            # Apply zero padding if the time stretched audio is not long enough to fill the
            # whole space, or crop the time stretched audio if it ended up too long.
            padded_samples = np.zeros(shape=samples.shape, dtype=samples.dtype)
            window = time_stretched_samples[: samples.shape[0]]
            actual_window_length = len(window)  # may be smaller than samples.shape[0]
            padded_samples[:actual_window_length] = window
            time_stretched_samples = padded_samples
        return time_stretched_samples


class PitchShift(BasicTransform):
    """Pitch shift the sound up or down without changing the tempo"""

    def __init__(self, min_semitones=-4, max_semitones=4, p=0.5):
        super().__init__(p)
        assert min_semitones >= -12
        assert max_semitones <= 12
        assert min_semitones <= max_semitones
        self.min_semitones = min_semitones
        self.max_semitones = max_semitones

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            self.parameters["num_semitones"] = random.uniform(
                self.min_semitones, self.max_semitones
            )

    def apply(self, samples, sample_rate):
        pitch_shifted_samples = librosa.effects.pitch_shift(
            samples, sample_rate, n_steps=self.parameters["num_semitones"]
        )
        return pitch_shifted_samples


class Shift(BasicTransform):
    """
    Shift the samples forwards or backwards.
    """

    def __init__(self, min_fraction=-0.5, max_fraction=0.5, rollover=True, p=0.5):
        """
        :param min_fraction: float, fraction of total sound length
        :param max_fraction: float, fraction of total sound length
        :param rollover: When set to True, samples that roll beyond the first or last position
            are re-introduced at the last or first. When set to False, samples that roll beyond
            the first or last position are discarded. In other words, rollover=False results in
            an empty space (with zeroes).
        :param p:
        """
        super().__init__(p)
        assert min_fraction >= -1
        assert max_fraction <= 1
        self.min_fraction = min_fraction
        self.max_fraction = max_fraction
        self.rollover = rollover

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            self.parameters["num_places_to_shift"] = int(
                round(
                    random.uniform(self.min_fraction, self.max_fraction) * len(samples)
                )
            )

    def apply(self, samples, sample_rate):
        num_places_to_shift = self.parameters["num_places_to_shift"]
        shifted_samples = np.roll(samples, num_places_to_shift)
        if not self.rollover:
            if num_places_to_shift > 0:
                shifted_samples[:num_places_to_shift] = 0.0
            elif num_places_to_shift < 0:
                shifted_samples[num_places_to_shift:] = 0.0
        return shifted_samples


class Normalize(BasicTransform):
    """
    Apply a constant amount of gain, so that highest signal level present in the sound becomes
    0 dBFS, i.e. the loudest level allowed if all samples must be between -1 and 1. Also known
    as peak normalization.
    """

    def __init__(self, p=0.5):
        super().__init__(p)

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            self.parameters["max_amplitude"] = np.amax(np.abs(samples))

    def apply(self, samples, sample_rate):
        normalized_samples = samples / self.parameters["max_amplitude"]
        return normalized_samples


class Trim(BasicTransform):
    """
    Trim leading and trailing silence from an audio signal using librosa.effects.trim
    """

    def __init__(self, top_db=20, p=1.0):
        super().__init__(p)
        self.top_db = top_db

    def apply(self, samples, sample_rate):
        samples, lens = librosa.effects.trim(samples, top_db=self.top_db)
        return samples


class Resample(BasicTransform):
    """
    Resample signal using librosa.core.resample

    To do downsampling only set both minimum and maximum sampling rate lower than original
    sampling rate and vice versa to do upsampling only.
    """

    def __init__(self, min_sample_rate=8000, max_sample_rate=44100, p=0.5):
        """
        :param min_sample_rate: int, Minimum sample rate
        :param max_sample_rate: int, Maximum sample rate
        :param p:
        """
        super().__init__(p)
        assert min_sample_rate <= max_sample_rate
        self.min_sample_rate = min_sample_rate
        self.max_sample_rate = max_sample_rate

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            self.parameters["target_sample_rate"] = random.randint(
                self.min_sample_rate, self.max_sample_rate
            )

    def apply(self, samples, sample_rate):
        samples = librosa.core.resample(
            samples,
            orig_sr=sample_rate,
            target_sr=self.parameters["target_sample_rate"],
        )
        return samples


class ClippingDistortion(BasicTransform):
    """Distort signal by clipping a random percentage of points

    The percentage of points that will ble clipped is drawn from a uniform distribution between
    the two input parameters min_percentile_threshold and max_percentile_threshold. If for instance
    30% is drawn, the samples are clipped if they're below the 15th or above the 85th percentile.
    """

    def __init__(self, min_percentile_threshold=0, max_percentile_threshold=40, p=0.5):
        """
        :param min_percentile_threshold: int, A lower bound on the total percent of samples that
            will be clipped
        :param max_percentile_threshold: int, A upper bound on the total percent of samples that
            will be clipped
        :param p:
        """
        super().__init__(p)
        assert min_percentile_threshold <= max_percentile_threshold
        assert 0 <= min_percentile_threshold <= 100
        assert 0 <= max_percentile_threshold <= 100
        self.min_percentile_threshold = min_percentile_threshold
        self.max_percentile_threshold = max_percentile_threshold

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            self.parameters["percentile_threshold"] = random.randint(
                self.min_percentile_threshold, self.max_percentile_threshold
            )

    def apply(self, samples, sample_rate):
        lower_percentile_threshold = int(self.parameters["percentile_threshold"] / 2)
        lower_threshold, upper_threshold = np.percentile(
            samples, [lower_percentile_threshold, 100 - lower_percentile_threshold]
        )
        samples = np.clip(samples, lower_threshold, upper_threshold)
        return samples


class AddBackgroundNoise(BasicTransform):
    """Mix in another sound, e.g. a background noise. Useful if your original sound is clean and
    you want to simulate an environment where background noise is present.

    Can also be used for mixup, as in https://arxiv.org/pdf/1710.09412.pdf

    A folder of (background noise) sounds to be mixed in must be specified. These sounds should
    ideally be at least as long as the input sounds to be transformed. Otherwise, the background
    sound will be repeated, which may sound unnatural.
    """

    def __init__(self, sounds_path=None, min_snr_in_db=3, max_snr_in_db=30, p=0.5):
        """
        :param sounds_path: Path to a folder that contains sound files to randomly mix in. These
            files can be flac, mp3, ogg or wav.
        :param min_snr_in_db: Minimum signal-to-noise ratio in dB
        :param max_snr_in_db: Maximum signal-to-noise ratio in dB
        :param p:
        """
        super().__init__(p)
        self.sound_file_paths = read_dir(sounds_path)
        self.sound_file_paths = [
            p
            for p in self.sound_file_paths
            if Path(p).suffix.lower() in {".flac", ".mp3", ".ogg", ".wav"}
        ]
        assert len(self.sound_file_paths) > 0
        self.min_snr_in_db = min_snr_in_db
        self.max_snr_in_db = max_snr_in_db

    @staticmethod
    @functools.lru_cache(maxsize=2)
    def __load_sound(file_path, sample_rate):
        return librosa.load(file_path, sample_rate)

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            self.parameters["snr_in_db"] = random.uniform(
                self.min_snr_in_db, self.max_snr_in_db
            )
            self.parameters["noise_file_path"] = random.choice(self.sound_file_paths)

            num_samples = len(samples)
            noise_sound, _ = self.__load_sound(
                self.parameters["noise_file_path"], sample_rate
            )

            num_noise_samples = len(noise_sound)
            min_noise_offset = 0
            max_noise_offset = max(0, num_noise_samples - num_samples - 1)
            self.parameters["noise_start_index"] = random.randint(
                min_noise_offset, max_noise_offset
            )
            self.parameters["noise_end_index"] = (
                self.parameters["noise_start_index"] + num_samples
            )

    def apply(self, samples, sample_rate):
        noise_sound, _ = self.__load_sound(
            self.parameters["noise_file_path"], sample_rate
        )
        noise_sound = noise_sound[
            self.parameters["noise_start_index"] : self.parameters["noise_end_index"]
        ]

        clean_rms = calculate_rms(samples)
        noise_rms = calculate_rms(noise_sound)
        desired_noise_rms = calculate_desired_noise_rms(
            clean_rms, self.parameters["snr_in_db"]
        )

        # Adjust the noise to match the desired noise RMS
        noise_sound = noise_sound * (desired_noise_rms / noise_rms)

        # Repeat the sound if it shorter than the input sound
        num_samples = len(samples)
        while len(noise_sound) < num_samples:
            noise_sound = np.concatenate((noise_sound, noise_sound))

        if len(noise_sound) > num_samples:
            noise_sound = noise_sound[0:num_samples]

        # Return a mix of the input sound and the background noise sound
        return samples + noise_sound


class AddShortNoises(BasicTransform):
    """Mix in various (bursts of overlapping) sounds with random pauses between. Useful if your
    original sound is clean and you want to simulate an environment where short noises sometimes
    occur.

    A folder of (noise) sounds to be mixed in must be specified.
    """

    def __init__(
        self,
        sounds_path=None,
        min_snr_in_db=0,
        max_snr_in_db=24,
        min_time_between_sounds=4.0,
        max_time_between_sounds=16.0,
        burst_probability=0.22,
        min_pause_factor_during_burst=0.1,
        max_pause_factor_during_burst=1.1,
        min_fade_in_time=0.005,
        max_fade_in_time=0.08,
        min_fade_out_time=0.01,
        max_fade_out_time=0.1,
        p=0.5,
    ):
        """
        :param sounds_path: Path to a folder that contains sound files to randomly mix in. These
            files can be flac, mp3, ogg or wav.
        :param min_snr_in_db: Minimum signal-to-noise ratio in dB. A lower value means the added
            sounds/noises will be louder.
        :param max_snr_in_db: Maximum signal-to-noise ratio in dB. A lower value means the added
            sounds/noises will be louder.
        :param min_time_between_sounds: Minimum pause time between the added sounds/noises
        :param max_time_between_sounds: Maximum pause time between the added sounds/noises
        :param burst_probability: The probability of adding an extra sound/noise that overlaps
        :param min_pause_factor_during_burst: Min value of how far into the current sound (as
            fraction) the burst sound should start playing. The value must be greater than 0.
        :param max_pause_factor_during_burst: Max value of how far into the current sound (as
            fraction) the burst sound should start playing. The value must be greater than 0.
        :param min_fade_in_time: Min sound/noise fade in time in seconds. Use a value larger
            than 0 to avoid a "click" at the start of the sound/noise.
        :param max_fade_in_time: Min sound/noise fade out time in seconds. Use a value larger
            than 0 to avoid a "click" at the start of the sound/noise.
        :param min_fade_out_time: Min sound/noise fade out time in seconds. Use a value larger
            than 0 to avoid a "click" at the end of the sound/noise.
        :param max_fade_out_time: Max sound/noise fade out time in seconds. Use a value larger
            than 0 to avoid a "click" at the end of the sound/noise.
        :param p: The probability of applying this transform
        """
        super().__init__(p)
        self.sound_file_paths = read_dir(sounds_path)
        self.sound_file_paths = [
            p
            for p in self.sound_file_paths
            if Path(p).suffix.lower() in {".flac", ".mp3", ".ogg", ".wav"}
        ]
        assert len(self.sound_file_paths) > 0
        assert min_snr_in_db <= max_snr_in_db
        assert min_time_between_sounds <= max_time_between_sounds
        assert 0.0 < burst_probability <= 1.0
        if burst_probability == 1.0:
            assert (
                min_pause_factor_during_burst > 0.0
            )  # or else an infinite loop will occur
        assert 0.0 < min_pause_factor_during_burst <= 1.0
        assert max_pause_factor_during_burst > 0.0
        assert max_pause_factor_during_burst >= min_pause_factor_during_burst
        assert min_fade_in_time >= 0.0
        assert max_fade_in_time >= 0.0
        assert min_fade_in_time <= max_fade_in_time
        assert min_fade_out_time >= 0.0
        assert max_fade_out_time >= 0.0
        assert min_fade_out_time <= max_fade_out_time

        self.min_snr_in_db = min_snr_in_db
        self.max_snr_in_db = max_snr_in_db
        self.min_time_between_sounds = min_time_between_sounds
        self.max_time_between_sounds = max_time_between_sounds
        self.burst_probability = burst_probability
        self.min_pause_factor_during_burst = min_pause_factor_during_burst
        self.max_pause_factor_during_burst = max_pause_factor_during_burst
        self.min_fade_in_time = min_fade_in_time
        self.max_fade_in_time = max_fade_in_time
        self.min_fade_out_time = min_fade_out_time
        self.max_fade_out_time = max_fade_out_time

    @staticmethod
    @functools.lru_cache(maxsize=64)
    def __load_sound(file_path, sample_rate):
        return librosa.load(file_path, sample_rate)

    def randomize_parameters(self, samples, sample_rate):
        super().randomize_parameters(samples, sample_rate)
        if self.parameters["should_apply"]:
            input_sound_duration = len(samples) / sample_rate

            current_time = 0
            global_offset = random.uniform(
                -self.max_time_between_sounds, self.max_time_between_sounds
            )
            current_time += global_offset
            sounds = []
            while current_time < input_sound_duration:
                sound_file_path = random.choice(self.sound_file_paths)
                sound, _ = self.__load_sound(sound_file_path, sample_rate)
                sound_duration = len(sound) / sample_rate

                # Ensure that the fade time is not longer than the duration of the sound
                fade_in_time = min(
                    sound_duration,
                    random.uniform(self.min_fade_in_time, self.max_fade_in_time),
                )
                fade_out_time = min(
                    sound_duration,
                    random.uniform(
                        self.min_fade_out_time, self.max_fade_out_time
                    )
                )

                sounds.append(
                    {
                        "fade_in_time": fade_in_time,
                        "start": current_time,
                        "end": current_time + sound_duration,
                        "fade_out_time": fade_out_time,
                        "file_path": sound_file_path,
                        "snr_in_db": random.uniform(
                            self.min_snr_in_db, self.max_snr_in_db
                        ),
                    }
                )

                # burst mode - add overlapping sounds
                while (
                    random.random() < self.burst_probability
                    and current_time < input_sound_duration
                ):
                    pause_factor = random.uniform(
                        self.min_pause_factor_during_burst,
                        self.max_pause_factor_during_burst,
                    )
                    pause_time = pause_factor * sound_duration
                    current_time = sounds[-1]["start"] + pause_time

                    if current_time >= input_sound_duration:
                        break

                    sound_file_path = random.choice(self.sound_file_paths)
                    sound, _ = self.__load_sound(sound_file_path, sample_rate)
                    sound_duration = len(sound) / sample_rate

                    fade_in_time = min(
                        sound_duration,
                        random.uniform(self.min_fade_in_time, self.max_fade_in_time),
                    )
                    fade_out_time = min(
                        sound_duration,
                        random.uniform(
                            self.min_fade_out_time, self.max_fade_out_time
                        )
                    )

                    sounds.append(
                        {
                            "fade_in_time": fade_in_time,
                            "start": current_time,
                            "end": current_time + sound_duration,
                            "fade_out_time": fade_out_time,
                            "file_path": sound_file_path,
                            "snr_in_db": random.uniform(
                                self.min_snr_in_db, self.max_snr_in_db
                            ),
                        }
                    )

                # wait until the last sound is done
                current_time += sound_duration

                # then add a pause
                pause_duration = random.uniform(
                    self.min_time_between_sounds, self.max_time_between_sounds
                )
                current_time += pause_duration

            self.parameters["sounds"] = sounds

    def apply(self, samples, sample_rate):
        num_samples = len(samples)
        noise_placeholder = np.zeros_like(samples)
        for sound_params in self.parameters["sounds"]:
            if sound_params["end"] < 0:
                # Skip a sound if it ended before the start of the input sound
                continue

            noise_samples, _ = self.__load_sound(sound_params["file_path"], sample_rate)

            # Apply fade in and fade out
            noise_gain = np.ones_like(noise_samples)
            fade_in_time_in_samples = int(sound_params["fade_in_time"] * sample_rate)
            fade_in_mask = np.linspace(0.0, 1.0, num=fade_in_time_in_samples)
            fade_out_time_in_samples = int(sound_params["fade_out_time"] * sample_rate)
            fade_out_mask = np.linspace(1.0, 0.0, num=fade_out_time_in_samples)
            noise_gain[: fade_in_mask.shape[0]] = fade_in_mask
            noise_gain[-fade_out_mask.shape[0] :] = np.minimum(
                noise_gain[-fade_out_mask.shape[0] :], fade_out_mask
            )
            noise_samples = noise_samples * noise_gain

            start_sample_index = int(sound_params["start"] * sample_rate)
            end_sample_index = start_sample_index + len(noise_samples)

            if start_sample_index < 0:
                # crop noise_samples: shave off a chunk in the beginning
                num_samples_to_shave_off = abs(start_sample_index)
                noise_samples = noise_samples[num_samples_to_shave_off:]
                start_sample_index = 0

            if end_sample_index > num_samples:
                # crop noise_samples: shave off a chunk in the end
                num_samples_to_shave_off = end_sample_index - num_samples
                noise_samples = noise_samples[
                    : len(noise_samples) - num_samples_to_shave_off
                ]
                end_sample_index = num_samples

            clean_rms = calculate_rms(samples[start_sample_index:end_sample_index])
            noise_rms = calculate_rms(noise_samples)
            if noise_rms > 0:
                desired_noise_rms = calculate_desired_noise_rms(
                    clean_rms, sound_params["snr_in_db"]
                )

                # Adjust the noise to match the desired noise RMS
                noise_samples = noise_samples * (desired_noise_rms / (noise_rms))

                noise_placeholder[start_sample_index:end_sample_index] += noise_samples

        # Return a mix of the input sound and the added sounds
        return samples + noise_placeholder

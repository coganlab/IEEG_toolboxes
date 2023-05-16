from typing import Union

import numpy as np
from mne.utils import fill_doc
from mne.epochs import BaseEpochs
from mne.evoked import Evoked
from mne.io import base
from mne.time_frequency import AverageTFR, EpochsTFR
from mne.utils import logger
from scipy.fft import fft, ifft

from ieeg.process import validate_type, ensure_int
from ieeg.calc.stats import find_outliers
from ieeg import Signal


def to_samples(time_length: Union[str, int], sfreq: float) -> int:
    """Convert a time length to a number of samples.

    Parameters
    ----------
    time_length : str | int
        The time length to convert. If a string, it must be a human-readable
        time, e.g. "10s".
    sfreq : float
        The sampling frequency.

    Returns
    -------
    samples : int
        The number of samples.
    """
    validate_type(time_length, (str, int))
    if isinstance(time_length, str):
        time_length = time_length.lower()
        err_msg = ('filter_length, if a string, must be a '
                   'human-readable time, e.g. "0.7s", or "700ms", not '
                   '"%s"' % time_length)
        low = time_length.lower()
        if low.endswith('us'):
            mult_fact = 1e-6
            time_length = time_length[:-2]
        elif low.endswith('ms'):
            mult_fact = 1e-3
            time_length = time_length[:-2]
        elif low[-1] == 's':
            mult_fact = 1
            time_length = time_length[:-1]
        elif low.endswith('sec'):
            mult_fact = 1
            time_length = time_length[:-3]
        elif low[-1] == 'm':
            mult_fact = 60
            time_length = time_length[:-1]
        elif low.endswith('min'):
            mult_fact = 60
            time_length = time_length[:-3]
        else:
            raise ValueError(err_msg)
        # now get the number
        try:
            time_length = float(time_length)
        except ValueError:
            raise ValueError(err_msg)
        time_length = max(int(np.ceil(time_length * mult_fact * sfreq)), 1)
    time_length = ensure_int(time_length, 'filter_length')
    return time_length


@fill_doc
def crop_pad(inst: Signal, pad: str, copy: bool = False) -> Signal:
    """Crop and pad an instance.

    Parameters
    ----------
    inst : instance of Raw, Epochs, or Evoked
        The instance to crop and pad.
    pad : str
        The amount of time to pad the instance. If a string, it must be a
        human-readable time, e.g. "10s".
    copy : bool, optional
        If True, a copy of x, filtered, is returned. Otherwise, it operates
        on x in place. Defaults to False.

    Returns
    -------
    inst : instance of Raw, Epochs, or Evoked
        The cropped and de-padded instance.
    """
    if copy:
        out = inst.copy()
    else:
        out = inst
    pad = to_samples(pad, inst.info['sfreq']) / inst.info['sfreq']
    out.crop(tmin=inst.tmin + pad, tmax=inst.tmax - pad)
    return out


def wavelet_scaleogram(inst: BaseEpochs, f_low: float = 2,
                       f_high: float = 1000, k0: int = 6, n_jobs: int = 1):
    data = inst.get_data()  # (trials X channels X timepoints)
    dt = 1 / inst.info['sfreq']
    s0 = 1 / (f_high+(0.1*f_high))  # the smallest resolvable scale
    n = data.shape[2]
    J1 = (np.log2(n * dt / s0)) / 0.2  # (J1 determines the largest scale)
    x = data - np.mean(data, axis=2, keepdims=True)

    k = np.arange(np.fix(n / 2)) + 1
    k = k * ((2 * np.pi) / (n * dt))
    kr = (-k).tolist()
    kr.reverse()
    k = np.array([0] + k.tolist() + kr)
    del kr

    f = fft(x)

    scale = s0 * np.power(2.,(np.arange(0,J1) * 0.2))
    fourier_factor = (4 * np.pi) / (k0 + np.sqrt(2 + np.square(k0)))
    coi = fourier_factor / np.sqrt(2)
    period = fourier_factor * scale
    xxx = np.min(np.where((1. / period) < f_low))
    period = np.flip(period[:xxx])
    scale = np.flip(scale[:xxx])

    scale1 = scale
    fscale = period.shape[0]
    period = fourier_factor * scale1

    expnt = -np.square(scale1[:, None] * k[None, :] - k0) / 2. * (k > 0.)
    norm = np.sqrt(scale1 * k[2]) * (np.power(np.pi, (-0.25))) * np.sqrt(n)
    daughter = norm[:, None] * np.exp(expnt)
    daughter = daughter * (k > 0.)

    wave = np.abs(ifft(f[:, :, None] * np.tile(daughter,
                                               (f.shape[0], 1, 1, 1)),
                       workers=n_jobs))

    # for ic, chn in enumerate(inst.ch_names):
    #     logger.info(chn)
    #
    #     tmp = ifft(f[:, None, ic] * np.tile(daughter, (f.shape[0], 1, 1)),
    #                workers=n_jobs)
    #     wave[:, ic] = np.abs(tmp)

    return EpochsTFR(inst.info, wave, inst.times, 1/period)


def _check_filterable(x: Union[Signal, np.ndarray],
                      kind: str = 'filtered',
                      alternative: str = 'filter') -> np.ndarray:
    # Let's be fairly strict about this -- users can easily coerce to ndarray
    # at their end, and we already should do it internally any time we are
    # using these low-level functions. At the same time, let's
    # help people who might accidentally use low-level functions that they
    # shouldn't use by pushing them in the right direction
    if isinstance(x, (base.BaseRaw, BaseEpochs, Evoked)):
        try:
            name = x.__class__.__name__
        except Exception:
            pass
        else:
            raise TypeError(
                'This low-level function only operates on np.ndarray '
                f'instances. To get a {kind} {name} instance, use a method '
                f'like `inst_new = inst.copy().{alternative}(...)` '
                'instead.')
    validate_type(x, (np.ndarray, list, tuple))
    x = np.asanyarray(x)
    if x.dtype != np.float64:
        raise ValueError('Data to be %s must be real floating, got %s'
                         % (kind, x.dtype,))
    return x
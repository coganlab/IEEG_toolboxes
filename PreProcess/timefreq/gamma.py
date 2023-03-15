from naplib.preprocessing import filterbank_hilbert
from PreProcess.timefreq.utils import BaseEpochs, Evoked, Signal, COLA
from PreProcess.utils.utils import get_mem, cpu_count
from mne.io import base, Raw
from mne import Epochs
from functools import singledispatch
import numpy as np


@singledispatch
def extract(data: np.ndarray, fs: int, passband: tuple[int, int] = (70, 150),
            copy: bool = True, n_jobs=-1) -> np.ndarray:
    """Extract gamma band envelope from data.

    Parameters
    ----------
    data : (np.ndarray, shape (..., channels)) | Signal
        Data to extract gamma envelope from. If Signal, will use the _data
        attribute.
    fs : int, optional
        Sampling frequency of data. If Signal, will use the data.info['sfreq'].
        Otherwise, must be provided.
    passband : tuple[int, int], optional
        Passband in Hz, high gamma band by default (70, 150)
    copy : bool, optional
        Whether to copy data or operate in place if False, by default True
    n_jobs : int, optional
        Number of jobs to run in parallel, by default all available cores

    Returns
    -------
    np.ndarray
        Gamma envelope.
    """

    if copy:
        in_data = data.copy()
    else:
        in_data = data

    passband = list(passband)
    env = np.zeros(in_data.shape)

    if len(in_data.shape) == 3:  # Assume shape is (trials, channels, time)
        for trial in range(in_data.shape[0]):
            _, out, _ = filterbank_hilbert(in_data[trial, :, :].T, fs,
                                           passband, n_jobs)
            env[trial, :, :] = np.sum(out, axis=-1).T
    elif len(in_data.shape) == 2:  # Assume shape is (channels, time)
        _, out, _ = filterbank_hilbert(in_data.T, fs, passband, n_jobs)
        env = np.sum(out, axis=-1).T
    else:
        raise ValueError("number of dims should be either 2 or 3, not {}"
                         "".format(len(in_data.shape)))

    return env.T


def _extract_inst(inst: Signal, fs: int, copy: bool, **kwargs) -> Signal:
    if fs is None:
        fs = inst.info['sfreq']
    if copy:
        sig = inst.copy()
    else:
        sig = inst

    sig._data = extract(sig._data, fs, copy=False, **kwargs)

    return sig


@extract.register
def _(inst: base.BaseRaw, fs: int = None,
      passband: tuple[int, int] = (70, 150),
      copy: bool = True, n_jobs=-1) -> Raw:
    return _extract_inst(inst, fs, copy, passband=passband, n_jobs=n_jobs)


@extract.register
def _(inst: BaseEpochs, fs: int = None,
      passband: tuple[int, int] = (70, 150),
      copy: bool = True, n_jobs=-1) -> Epochs:
    return _extract_inst(inst, fs, copy, passband=passband, n_jobs=n_jobs)


@extract.register
def _(inst: Evoked, fs: int = None,
      passband: tuple[int, int] = (70, 150),
      copy: bool = True, n_jobs=-1) -> Evoked:
    return _extract_inst(inst, fs, copy, passband=passband, n_jobs=n_jobs)


def _my_hilt(x, fs, Wn=(1, 150), n_jobs=-1):

    # Set default window function and threshold
    cfs = get_centers(x, Wn)
    n_times = x.shape[0]
    chunk_size = n_times * x.shape[1] * len(cfs)
    n_samples = int(min([chunk_size, get_mem()]) /
                    (cpu_count() * x.shape[1] * len(cfs)))
    n_overlap = (n_samples + 1) // 2
    x_out = np.zeros_like(x)
    idx = [0]

    # Define how to process a chunk of data
    def process(x_):
        out = filterbank_hilbert(x_, fs, Wn, 1)
        env = np.sum(out[1], axis=-1)
        return (env,)  # must return a tuple

    # Define how to store a chunk of fully processed data (it's trivial)
    def store(x_):
        stop = idx[0] + x_.shape[-1]
        x_out[..., idx[0]:stop] += x_
        idx[0] = stop

    COLA(process, store, n_times, n_samples, n_overlap, fs,
         n_jobs=n_jobs).feed(x)
    assert idx[0] == n_times
    return x_out


def get_centers(x, Wn):

    # create filter bank
    a = np.array([np.log10(0.39), 0.5])
    f0 = 0.018
    octSpace = 1. / 7
    minf, maxf = Wn
    if minf >= maxf:
        raise ValueError(
            f'Upper bound of frequency range must be greater than lower '
            f'bound, but got lower bound of {minf} and upper bound of {maxf}')
    maxfo = np.log2(maxf / f0)  # octave of max freq

    cfs = [f0]
    sigma_f = 10 ** (a[0] + a[1] * np.log10(cfs[-1]))

    while np.log2(cfs[-1] / f0) < maxfo:

        if cfs[-1] < 4:
            cfs.append(cfs[-1] + sigma_f)
        else:  # switches to log spacing at 4 Hz
            cfo = np.log2(cfs[-1] / f0)  # current freq octave
            cfo += octSpace  # new freq octave
            cfs.append(f0 * (2 ** (cfo)))

        sigma_f = 10 ** (a[0] + a[1] * np.log10(cfs[-1]))

    cfs = np.array(cfs)
    if np.logical_and(cfs >= minf, cfs <= maxf).sum() == 0:
        raise ValueError(
            f'Frequency band is too narrow, so no filters in filterbank are '
            f'placed inside. Try a wider frequency band.')

    cfs = cfs[np.logical_and(cfs >= minf,
                             cfs <= maxf)]  # choose those that lie in the
    # input freqRange
    return cfs
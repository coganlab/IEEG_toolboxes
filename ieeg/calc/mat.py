import logging
from collections.abc import Iterable
import functools
import itertools
from copy import deepcopy

import mne

from ieeg.calc.reshape import concatenate_arrays
from ieeg import Signal

import numpy as np
from numpy.matlib import repmat
from numpy.typing import ArrayLike


def iter_nest_dict(d: dict, _lvl: int = 0, _coords=()):
    """Iterate over a nested dictionary, yielding the key and value.

    Parameters
    ----------
    d : dict
        The dictionary to iterate over.

    Yields
    ------
    tuple
        The key and value of the dictionary.

    Examples
    --------
    >>> d = {'a': {'b': 1, 'c': 2}, 'd': {'e': 3, 'f': 4}}
    >>> for k, v in iter_nest_dict(d):
    ...     print(k, v)
    ('a', 'b') 1
    ('a', 'c') 2
    ('d', 'e') 3
    ('d', 'f') 4
    """
    for k, v in d.items():
        if isinstance(v, dict):
            yield from iter_nest_dict(v, _lvl + 1, _coords + (k,))
        elif isinstance(v, np.ndarray):
            yield from iter_nest_dict({i: val for i, val in enumerate(v)
                                       }, _lvl + 1, _coords + (k,))
        else:
            yield _coords + (k,), v


class LabeledArray(np.ndarray):
    """ A numpy array with labeled dimensions, acting like a dictionary.

    A numpy array with labeled dimensions. This class is useful for storing
    data that is not easily represented in a tabular format. It acts as a
    nested dictionary but its values map to elements of a stored numpy array.

    Parameters
    ----------
    input_array : array_like
        The array to store in the LabeledArray.
    labels : tuple[tuple[str, ...], ...], optional
        The labels for each dimension of the array, by default ().
    delimiter : str, optional
        The delimiter to use when combining labels, by default '-'
    **kwargs
        Additional arguments to pass to np.asarray.

    Attributes
    ----------
    labels : tuple[tuple[str, ...], ...]
        The labels for each dimension of the array.
    array : np.ndarray
        The array stored in the LabeledArray.

    Examples
    --------
    >>> import numpy as np
    >>> from ieeg.calc.mat import LabeledArray
    >>> arr = np.ones((2, 3, 4), dtype=int)
    >>> labels = (('a', 'b'), ('c', 'd', 'e'), ('f', 'g', 'h', 'i'))
    >>> la = LabeledArray(arr, labels)
    >>> la
    array([[[1, 1, 1, 1],
            [1, 1, 1, 1],
            [1, 1, 1, 1]],
    <BLANKLINE>
           [[1, 1, 1, 1],
            [1, 1, 1, 1],
            [1, 1, 1, 1]]])
    labels(['a', 'b']
           ['c', 'd', 'e']
           ['f', 'g', 'h', 'i'])
    >>> la.to_dict() # doctest: +ELLIPSIS
    {'a': {'c': {'f': 1, 'g': 1, 'h': 1, 'i': 1}, 'd': {'f': 1, 'g': 1,...
    >>> la['a', 'c', 'f'] = 2
    >>> la['a', 'c', 'f']
    2
    >>> la['a', 'c']
    array([2, 1, 1, 1])
    labels(['f', 'g', 'h', 'i'])
    >>> la['a']
    array([[2, 1, 1, 1],
           [1, 1, 1, 1],
           [1, 1, 1, 1]])
    labels(['c', 'd', 'e']
    	   ['f', 'g', 'h', 'i'])
    >>> la['a','d'] = np.array([3,3,3,3])
    >>> la[('a','b'), :] # doctest: +ELLIPSIS
    array([[[2, 1, 1, 1],
            [3, 3, 3, 3],
            [1, 1, 1, 1]],
    <BLANKLINE>
           [[1, 1, 1, 1],
            [1, 1, 1, 1],
            [1, 1, 1, 1]]])
    labels(['a', 'b']
    	   ['c', 'd', 'e']
    	   ['f', 'g', 'h', 'i'])
    >>> la[np.array([False, True]),]
    array([[[1, 1, 1, 1],
            [1, 1, 1, 1],
            [1, 1, 1, 1]]])
    labels(['b']
           ['c', 'd', 'e']
           ['f', 'g', 'h', 'i'])
    >>> la[(0, 1)]
    array([3, 3, 3, 3])
    labels(['f', 'g', 'h', 'i'])
    >>> la[0, 1]
    array([3, 3, 3, 3])
    labels(['f', 'g', 'h', 'i'])
    >>> la[(0, 1),]
    array([[[2, 1, 1, 1],
            [3, 3, 3, 3],
            [1, 1, 1, 1]],
    <BLANKLINE>
           [[1, 1, 1, 1],
            [1, 1, 1, 1],
            [1, 1, 1, 1]]])
    labels(['a', 'b']
           ['c', 'd', 'e']
           ['f', 'g', 'h', 'i'])
    >>> np.nanmean(la, axis=(-2, -1))
    array([1.75, 1.  ])
    labels(['a', 'b'])
    >>> arr = np.arange(24).reshape((2, 3, 4))
    >>> labels = (('a', 'b'), ('c', 'd', 'e'), ('f', 'g', 'h', 'i'))
    >>> ad = LabeledArray(arr, labels)
    >>> ad[None, 'a']
    array([[[ 0,  1,  2,  3],
            [ 4,  5,  6,  7],
            [ 8,  9, 10, 11]]])
    labels(['1']
           ['c', 'd', 'e']
           ['f', 'g', 'h', 'i'])
    >>> ad['b', 0, np.array([[1,2], [0,3]])]
    array([[13, 14],
           [12, 15]])
    labels(['g-h', 'f-i']
           ['g-f', 'h-i'])

    Notes
    -----
    Multiple array indices and string slice objects are not supported. If you
    want to use array indices, you should use them one at a time.

    References
    ----------
    [1] https://numpy.org/doc/stable/user/basics.subclassing.html
    """
    __slots__ = ['labels', '__dict__']

    def __new__(cls, input_array, labels: list[tuple[str, ...], ...] = (),
                delimiter: str = '-', **kwargs):
        obj = np.asarray(input_array, **kwargs).view(cls)
        labels = list(labels)
        for i in range(obj.ndim):
            if len(labels) < i + 1:
                labels.append(tuple(range(obj.shape[i])))
        obj.labels = list(map(lambda l: Labels(l, delimiter), labels))
        assert tuple(map(len, obj.labels)) == obj.shape, \
            f"labels must have the same length as the shape of the array, " \
            f"instead got {tuple(map(len, obj.labels))} and {obj.shape}"
        return obj

    def __array_finalize__(self, obj, *args, **kwargs):
        if obj is None:
            return
        self.labels = getattr(obj, 'labels', kwargs.pop('labels', ()))
        super().__array_finalize__(obj, *args, **kwargs)

    def __array_ufunc__(self, ufunc, method, *inputs, out=None, **kwargs):
        la_inputs = (i for i in inputs if isinstance(i, LabeledArray))
        labels = deepcopy(next(la_inputs).labels)
        inputs = tuple(i.view(np.ndarray) if isinstance(i, LabeledArray)
                       else i for i in inputs)
        if out is not None:
            kwargs['out'] = tuple(o.view(np.ndarray) if
                                  isinstance(o, LabeledArray)
                                  else o for o in out)
        if method == 'reduce':
            axis = kwargs.get('axis', None)
            if axis is None:
                axis = range(inputs[0].ndim)
            elif np.isscalar(axis):
                axis = (axis,)
            else:
                axis = tuple(axis)
            i = 0
            for ax in axis:
                if ax > 0:
                    ax -= i
                labels = list(labels)
                if kwargs.get('keepdims', False):
                    labels[ax] = ("-".join(labels[ax]),)
                else:
                    labels.pop(ax)
                    i += 1
                labels = tuple(labels)

        outputs = super().__array_ufunc__(ufunc, method, *inputs, **kwargs)
        if isinstance(outputs, tuple):
            outputs = tuple(LabeledArray(o, labels)
                            if isinstance(o, np.ndarray)
                            else o for o in outputs)
        elif isinstance(outputs, np.ndarray):
            outputs = LabeledArray(outputs, labels)
        return outputs

    @property
    def T(self):
        return LabeledArray(self.__array__().T, self.labels[::-1])

    def swapaxes(self, axis1, axis2):
        new = list(self.labels)
        new[axis1], new[axis2] = new[axis2], new[axis1]
        return LabeledArray(super().swapaxes(axis1, axis2), new)

    @classmethod
    def from_dict(cls, data: dict, **kwargs) -> 'LabeledArray':
        """Create a LabeledArray from a dictionary.

        Parameters
        ----------
        data : dict
            The dictionary to convert to a LabeledArray.

        Returns
        -------
        LabeledArray
            The LabeledArray created from the dictionary.

        Examples
        --------
        >>> data = {'a': {'b': {'c': 1}}}
        >>> LabeledArray.from_dict(data, dtype=int) # doctest: +ELLIPSIS
        array([[[1]]])
        labels(['a']
               ['b']
               ['c'])
        >>> data = {'a': {'b': {'c': 1}}, 'd': {'b': {'c': 2, 'e': 3}}}
        >>> LabeledArray.from_dict(data) # doctest: +ELLIPSIS
        array([[[ 1., nan]],
        <BLANKLINE>
               [[ 2.,  3.]]])
        labels(['a', 'd']
               ['b']
               ['c', 'e'])
        """

        arr = inner_array(data)
        keys = inner_all_keys(data)
        return cls(arr, keys, **kwargs)

    @classmethod
    def from_signal(cls, sig: Signal, **kwargs) -> 'LabeledArray':
        """Create a LabeledArray from a Signal.

        Parameters
        ----------
        sig : Signal
            The Signal to convert to a LabeledArray.

        Returns
        -------
        LabeledArray
            The LabeledArray created from the Signal.

        Examples
        --------
        >>> from bids import BIDSLayout
        >>> from ieeg.io import raw_from_layout
        >>> from ieeg.navigate import trial_ieeg
        >>> bids_root = mne.datasets.epilepsy_ecog.data_path()
        >>> layout = BIDSLayout(bids_root)
        >>> raw = raw_from_layout(layout, subject="pt1", preload=True,
        ... extension=".vhdr", verbose=False)
        Reading 0 ... 269079  =      0.000 ...   269.079 secs...
        >>> LabeledArray.from_signal(raw, dtype=float) # doctest: +ELLIPSIS
        array([[-8.98329883e-06,  8.20419238e-06,  7.42294287e-06, ...,
                 1.07177293e-09,  1.07177293e-09,  1.07177293e-09],
               [ 2.99222000e-04,  3.03518844e-04,  2.96878250e-04, ...,
                 3.64667153e-09,  3.64667153e-09,  3.64667153e-09],
               [ 2.44140953e-04,  2.30078469e-04,  2.19140969e-04, ...,
                 3.85053724e-10,  3.85053724e-10,  3.85053724e-10],
               ...,
               [ 1.81263844e-04,  1.74232594e-04,  1.56263875e-04, ...,
                 1.41283798e-08,  1.41283798e-08,  1.41283798e-08],
               [ 2.25390219e-04,  2.16015219e-04,  1.91405859e-04, ...,
                -2.91418821e-10, -2.91418821e-10, -2.91418821e-10],
               [ 3.14092313e-04,  3.71123375e-04,  3.91826437e-04, ...,
                 3.07457047e-08,  3.07457047e-08,  3.07457047e-08]])
        labels(['G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G8', 'G9', ...
        >>> epochs = trial_ieeg(raw, "AD1-4, ATT1,2", (-1, 2), preload=True,
        ... verbose=False)
        >>> LabeledArray.from_signal(epochs, dtype=float) # doctest: +ELLIPSIS
        array([[[ 0.00021563,  0.00021563,  0.00020703, ..., -0.00051211,
                 -0.00051445, -0.00050351],
                [-0.00030586, -0.00030625, -0.00031171, ..., -0.00016054,
                 -0.00015976, -0.00015664],
                [-0.00010781, -0.00010469, -0.00010859, ...,  0.00026719,
                  0.00027695,  0.00030156],
                ...,
                [-0.00021483, -0.00021131, -0.00023084, ..., -0.00034295,
                 -0.00032381, -0.00031444],
                [-0.00052188, -0.00052852, -0.00053125, ..., -0.00046211,
                 -0.00047148, -0.00047891],
                [-0.00033708, -0.00028005, -0.00020934, ..., -0.00040934,
                 -0.00042341, -0.00040973]]])
        labels(['AD1-4, ATT1,2']
               ['G1', 'G2', 'G3', 'G4', 'G5', 'G6', 'G7', 'G8', 'G9', ...
               [-1.0, -0.999, -0.998, -0.997, -0.996, -0.995, -0.994, ...

        """

        arr = sig.get_data()
        match sig:
            case mne.io.base.BaseRaw():
                labels = [sig.ch_names, sig.times]
            case mne.BaseEpochs():
                events = events_in_order(sig)
                labels = [events, sig.ch_names, sig.times]
            case mne.evoked.Evoked():
                labels = [sig.ch_names, sig.times]
            case mne.time_frequency.EpochsTFR():
                events = events_in_order(sig)
                labels = [events, sig.ch_names, sig.freqs, sig.times]
            case mne.time_frequency.AverageTFR():
                labels = [sig.ch_names, sig.freqs, sig.times]
            case _:
                raise TypeError(f"Unexpected data type: {type(sig)}")
        return cls(arr, labels, **kwargs)

    def _parse_index(self, keys: list):
        ndim = self.ndim
        new_keys = [range(self.shape[i]) for i in range(ndim)]
        dim = 0
        newaxis_count = 0
        for i, key in enumerate(keys):
            key_type = type(key)
            if np.issubdtype(key_type, str):
                key = self.labels[dim - newaxis_count].find(key)
                keys[i] = key # set original keys as well
            elif key is Ellipsis:
                num_ellipsis_dims = ndim - len(keys) + 1
                while dim < num_ellipsis_dims:
                    dim += 1
                continue
            elif key_type is slice:
                key = new_keys[dim][key]
            elif key is np.newaxis or key is None:
                new_keys.insert(dim, None)
                newaxis_count += 1
                dim += 1
                continue
            elif key_type in (list, tuple) or np.issubdtype(key_type, np.ndarray):
                key = list(key)
                for j, k in enumerate(key):
                    if np.issubdtype(type(k), str):
                        key[j] = self.labels[dim - newaxis_count].find(k)
                if np.issubdtype(key_type, np.ndarray):
                    keys[i] = np.array(key)
                else:
                    keys[i] = key_type(key)

            if np.isscalar(key):  # key should be an int
                while key < 0:
                    key += self.shape[dim - newaxis_count]
            new_keys[dim] = key
            dim += 1
        return tuple(new_keys)

    def _to_coords(self, orig_keys):
        if np.isscalar(orig_keys):
            keys = [orig_keys]
            l_keys = self._parse_index(keys)
            return keys[0], l_keys
        else:
            keys = list(orig_keys)
            l_keys = self._parse_index(keys)
            return tuple(keys), l_keys

    def __getitem__(self, orig_keys):
        keys, label_keys = self._to_coords(orig_keys)
        new_labels = []

        j = 0
        n_idx = []
        for i, label_key in enumerate(label_keys):
            if label_key is None:
                new_labels.append(np.array(['1']))
                j += 1
                n_idx.append(i)
            elif not np.isscalar(label_key):
                new_labels.append(self.labels[i - j][label_key])
                if new_labels[-1].ndim > 1:
                    l = new_labels.pop(-1)
                    new_labels.extend(l.decompose())

        out = super().__getitem__(keys)

        if out.ndim == 0:
            return out[()]
        assert out.ndim == len(new_labels), \
            f"keys must have the same length as the number of dimensions, " \
            f"instead got {out.ndim} and {len(new_labels)}"

        setattr(out, 'labels', new_labels)
        return out

    def __setitem__(self, keys, value):
        keys, _ = self._to_coords(keys)
        super().__setitem__(keys, value)

    def __repr__(self):
        return repr(self.__array__()) + f"\nlabels({self._label_formatter()})"

    def __str__(self):
        return str(self.__array__()) + f"\nlabels({self._label_formatter()})"

    def _label_formatter(self):
        liststr = lambda x: f"\n       ".join(x)
        return liststr([str(l) for l in self.labels])

    def memory(self):
        size = self.nbytes
        for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']:
            if size < 1024.0 or unit == 'PiB':
                break
            size /= 1024.0
        return size, unit

    def __eq__(self, other):
        if isinstance(other, LabeledArray):
            return np.array_equal(self, other, True) and \
                self.labels == other.labels
        else:
            return self.__array__().__eq__(other)

    def __ne__(self, other):
        return not self.__eq__(other)

    def to_dict(self) -> dict:
        """Convert to a dictionary."""
        out = {}
        for k, v in self.items():
            if len(self.labels) > 1:
                out[k] = v.to_dict()
            elif np.isnan(v).all():
                continue
            else:
                out[k] = v
        return out

    def items(self):
        return zip(self.keys(), self.values())

    def keys(self):
        return (lab for lab in self.labels[0])

    def values(self):
        return (a for a in self)

    def _reshape(self, shape, order='C') -> 'LabeledArray':
        """Reshape the array.

        Parameters
        ----------
        shape : tuple[int, ...]
            The new shape of the array.
        order : str, optional
            The order to reshape the array in, by default 'C'

        Returns
        -------
        LabeledArray
            The reshaped LabeledArray.

        Examples
        --------
        >>> data = {'a': {'b': {'c': 1}}}
        >>> ad = LabeledArray.from_dict(data, dtype=int)
        >>> ad.reshape((1, 1, 1))
        array([[[1]]])
        labels(['a']
               ['b']
               ['c'])
        >>> arr = np.arange(24).reshape((2, 3, 4))
        >>> labels = [('a', 'b'), ('c', 'd', 'e'), ('f', 'g', 'h', 'i')]
        >>> ad = LabeledArray(arr, labels)
        >>> ad.reshape((6, 4))
        array([[ 0,  1,  2,  3],
               [ 4,  5,  6,  7],
               [ 8,  9, 10, 11],
               [12, 13, 14, 15],
               [16, 17, 18, 19],
               [20, 21, 22, 23]])
        labels(['a', 'b']
               ['c', 'd', 'e']
               ['f', 'g', 'h', 'i'])
        >>> ad.reshape((6, 4), 'F').labels
        [('a-c', 'b-c', 'a-d', 'b-d', 'a-e', 'b-e'), ('f', 'g', 'h', 'i')]
        >>> ad.reshape((2, 12)).labels # doctest: +ELLIPSIS
        [array(['a', 'b'], dtype='<U1'), array(['c', 'd', 'e'], dtype='<U1')...
        >>> arr = np.arange(10)
        >>> labels = [list(map(str, arr))]
        >>> ad = LabeledArray(arr, labels)
        >>> ad.reshape((2, 5)).labels
        [array(['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'], ...
        >>> ad.reshape((1, 2, 5)).labels
        [array(['0', '1', '2', '3', '4', '5', '6', '7', '8', '9'], ...
        """
        new_array = super().reshape(*shape, order=order)
        new_labels = label_reshape(self.labels, shape, order)
        return LabeledArray(new_array, new_labels)

    def prepend_labels(self, pre: str, level: int) -> 'LabeledArray':
        """Prepend a string to all labels at a given level.

        Parameters
        ----------
        pre : str
            The string to prepend to all labels.
        level : int
            The level to prepend the string to.

        Returns
        -------
        LabeledArray
            The LabeledArray with the prepended labels.

        Examples
        --------
        >>> data = {'a': {'b': {'c': 1}}}
        >>> ad = LabeledArray.from_dict(data, dtype=int)
        >>> ad.prepend_labels('pre-', 1) # doctest: +ELLIPSIS
        array([[[1]]])
        labels(['a']
               ['pre-b']
               ['c'])
        """
        assert 0 <= level < self.ndim, "level must be >= 0 and < ndim"
        self.labels[level] = tuple(pre + lab for lab in self.labels[level])
        return LabeledArray(self.view(np.ndarray), self.labels)

    def combine(self, levels: tuple[int, int]) -> 'LabeledArray':
        """Combine any levels of a LabeledArray into the lower level

        Takes the input LabeledArray and rearranges its dimensions.

        Parameters
        ----------
        levels : tuple[int, int]
            The levels to combine, e.g. (0, 1) will combine the 1st and 2nd
            level of the array labels into one level at the 2nd level.
        delim : str, optional
            The delimiter to use when combining labels, by default '-'

        Returns
        -------
        LabeledArray
            The combined LabeledArray

        Examples
        --------
        >>> data = {'a': {'b': {'c': 1}}}
        >>> ad = LabeledArray.from_dict(data, dtype=int)
        >>> ad.combine((0, 2))
        array([[1]])
        labels(['b']
               ['a-c'])
        >>> ad2 = LabeledArray([[[1,2],[3,4]],[[5,6],[7,8]]],
        ... labels=[('a', 'b'), ('c', 'd'), ('e', 'f')])
        >>> ad2['a', : , 'e']
        array([1, 3])
        labels(['c', 'd'])
        >>> ad2.combine((0, 2))
        array([[1, 2, 5, 6],
               [3, 4, 7, 8]])
        labels(['c', 'd']
               ['a-e', 'a-f', 'b-e', 'b-f'])
        >>> np.mean(ad2.combine((0, 2)), axis=1)
        array([3.5, 5.5])
        labels(['c', 'd'])
        >>> np.mean(ad2, axis=(0, 2))
        array([3.5, 5.5])
        labels(['c', 'd'])
        """

        assert levels[0] >= 0, "first level must be >= 0"
        assert levels[1] > levels[0], "second level must be > first level"

        new_labels = list(self.labels)
        new_labels.pop(levels[0])

        new_labels[levels[1] - 1] = (
                self.labels[levels[0]] @ self.labels[levels[1]]).flatten()

        arrs = [np.take(self.__array__(), axis=levels[0], indices=i)
                for i in range(self.shape[levels[0]])]
        new_array = concatenate_arrays(arrs, axis=levels[1] - 1)

        return LabeledArray(new_array, new_labels, dtype=self.dtype)

    def take(self, indices, axis=None, **kwargs):

        idx = [slice(None)] * self.ndim

        if axis is None:
            return self.flat[indices]
        elif isinstance(axis, int):
            idx[axis] = indices
        elif len(indices) == len(axis):
            for i, ax in enumerate(axis):
                idx[ax] = indices[i]
        else:
            raise ValueError("indices and axis must have the same length")

        return self[tuple(idx)]

    def dropna(self) -> 'LabeledArray':
        """Remove all nan values from the array.

        Scans each column along any axis and removes all rows that contain
        only nan values.

        Returns
        -------
        LabeledArray
            The array with all nan values removed.

        Examples
        --------
        >>> data = {'a': {'b': {'c': 1., 'd': np.nan}}}
        >>> ad = LabeledArray.from_dict(data)
        >>> ad.dropna()
        array([[[1.]]])
        labels(['a']
               ['b']
               ['c'])
        >>> ad2 = LabeledArray([[[1,2],[3,4]],[[4,5],[6,7]],
        ... [[np.nan, np.nan], [np.nan, np.nan]]])
        >>> ad2.dropna()
        array([[[1., 2.],
                [3., 4.]],
        <BLANKLINE>
               [[4., 5.],
                [6., 7.]]])
        labels([0, 1]
               [0, 1]
               [0, 1])
        """
        new_labels = list(self.labels)
        idx = []
        for i in range(self.ndim):
            axes = tuple(j for j in range(self.ndim) if j != i)
            mask = np.all(np.isnan(np.array(self)), axis=axes)
            if np.any(mask):
                new_labels[i] = tuple(np.array(new_labels[i])[~mask])
            idx.append(~mask)
        index = np.ix_(*idx)
        new_array = LabeledArray(np.array(self)[index], new_labels)
        return new_array

    def appended(self, arr: 'LabeledArray', axis: int = 0) -> 'LabeledArray':
        """Append a LabeledArray to the end of this LabeledArray.

        Parameters
        ----------
        arr : LabeledArray
            The LabeledArray to append to the end of this LabeledArray.
        axis : int, optional
            The axis to append the array along, by default 0

        Returns
        -------
        LabeledArray
            The LabeledArray with the appended array.

        Examples
        --------
        >>> data1 = {'a': {'b': 1}}
        >>> data2 = {'a': {'c': 2}}
        >>> ad1 = LabeledArray.from_dict(data1, dtype=int)
        >>> ad2 = LabeledArray.from_dict(data2, dtype=int)
        >>> ad1.appended(ad2, 1)
        LabeledArray([[1, 2]])
        labels=(('a',), ('b', 'c'))
        """
        self.labels += arr.labels[axis]
        new_array = concatenate_arrays([self, arr], axis).astype(self.dtype)
        return LabeledArray(new_array, self.labels)


class Labels(np.ndarray):
    """A class for storing labels for a LabeledArray."""
    def __new__(cls, input_array: ArrayLike, delim: str = '-'):
        arr = np.asarray(input_array)
        # Determine the smallest data type that can represent the data
        if np.issubdtype(arr.dtype, np.number):
            if np.all(np.mod(arr, 1) == 0):
                # All values are integers
                dtype = 'int'
            else:
                # All values are numbers (integers or floats)
                dtype = 'float'
        else:
            # Not all values are numbers, so use objects
            dtype = f'U{np.max(np.char.str_len(arr))}'
        # Input array is an already formed ndarray instance
        # We first cast to be our class type
        obj = np.asarray(input_array, dtype=dtype).view(cls)
        setattr(obj, 'delimiter', delim)
        arr = obj.__array__().flatten()
        assert len(np.unique(arr)) == len(arr), f"Labels {arr} must be unique"
        return obj

    def __array_finalize__(self, obj):
        if obj is None: return
        self.delimiter = getattr(obj, 'delimiter', '-')

    def __str__(self):
        return self.tolist().__str__()

    def __repr__(self):
        return self.tolist().__repr__()

    def __matmul__(self, other):
        if not isinstance(other, Labels):
            raise NotImplementedError("Only Labels @ Labels is supported")
        s_str, o_str = self.astype(str), other.astype(str)

        # Convert the arrays to 2D
        s_str_2d = s_str[..., None]
        o_str_2d = np.char.add(self.delimiter, o_str[None])

        # Use broadcasting to create a result array with combined strings
        result = np.char.add(s_str_2d, o_str_2d)
        return Labels(result)

    def decompose(self) -> list['Labels', ...]:
        """Decompose a Labels object into a list of 1d Labels objects."""
        new_labels = [[None for _ in range(s)] for s in self.shape]
        for i, dim in enumerate(self.shape):
            for j in range(dim):
                row = np.take(self, j, axis=i).reshape(-1, self.ndim)
                common = _longest_common_substring(tuple(map(tuple, row)))
                if len(common) == 0:
                    common = list(set(d[i] for d in row))
                new_labels[i][j] = self.delimiter.join(common)
        return list(map(Labels, new_labels))

    def find(self, value) -> int:
        """Get the index of the first instance of a value in the Labels"""
        idx = np.where(self == value)[0]
        if len(idx) == 0:
            raise IndexError(f"{value} not found in {arr}")
        else:
            return int(idx[0])


def label_reshape(labels: list[tuple[str, ...], ...], shape: tuple[int, ...],
                  order: str = 'C', delim: str = '-'
                  ) -> list[tuple[str, ...], ...]:
    """Reshape the labels of a LabeledArray.

    Takes the labels corresponding to the shape of an array and reshapes them
    into the new shape. This is accomplished by 'flattening' the labels and
    then reassigning and reducing them in the new shape.

    Parameters
    ----------
    labels : tuple[tuple[str, ...], ...]
        The labels to reshape.
    shape : tuple[int, ...]
        The new shape of the labels.
    order : str, optional
        The order to reshape the labels in, by default 'C'
    delim : str, optional
        The delimiter to use when combining labels, by default '-'

    Returns
    -------
    tuple[tuple[str, ...], ...]
        The reshaped labels.

    Examples
    --------
    >>> labels = [('az', 'b'), ('c', 'd', 'e'), ('f', 'g', 'h', 'i')]
    >>> label_reshape(labels, (6, 4))
    [('az-c', 'az-d', 'az-e', 'b-c', 'b-d', 'b-e'), ('f', 'g', 'h', 'i')]
    >>> label_reshape(labels, (6, 4), 'F')
    [('az-c', 'b-c', 'az-d', 'b-d', 'az-e', 'b-e'), ('f', 'g', 'h', 'i')]
    >>> label_reshape(labels, (2, 12)) # doctest: +ELLIPSIS
    [('az', 'b'), ('c-f', 'c-g', 'c-h', 'c-i', 'd-f', 'd-g', 'd-h', 'd-i'...
    >>> label_reshape(labels, (3, 2, 4))
    [('az', 'az-b', 'b'), ('c-d-e', 'c-d-e'), ('f', 'g', 'h', 'i')]
    >>> labels = labels[:2] + [(1, 2, 3, 4),]
    >>> label_reshape(labels, (6, 4))
    [('az-c', 'az-d', 'az-e', 'b-c', 'b-d', 'b-e'), (1, 2, 3, 4)]
    >>> label_reshape(labels, (2, 12), 'F') # doctest: +ELLIPSIS
    [('az', 'b'), ('c-1', 'd-1', 'e-1', 'c-2', 'd-2', 'e-2', 'c-3', 'd-3', ...
    >>> label_reshape(labels, (1, 1, 2, 12))
    [(1,), (1,), ('az-1', 'b-1'), ('c-1-1', 'c-2-1', 'c-3-1', 'c-4-1', ...
    """
    labels = labels.copy()
    types = list(map(lambda x: type(x[0]), labels))
    m = 0
    for i, l in enumerate(labels):
        labels[i] = tuple(map(str, l))
        m = max((max(map(len, labels[i])), m))
    count = int(np.multiply.reduce(shape))

    if order == 'F':
        prod = ((*reversed(x),) for x in itertools.product(*reversed(labels)))
    else:
        prod = itertools.product(*labels)

    # Pre-allocate the output array
    out = np.empty(count, dtype=np.dtype((f'U{m}', len(labels))))

    # Fill the output array using a loop
    for i, x in enumerate(prod):
        if len(x) == 1:
            x = x[0]
        out[i] = x
    out = out.reshape(*shape, len(labels), order=order)

    # now that temp is a char array of corresponding labels, we can factor the
    # matrix into the new labels
    new_labels = [[None for _ in range(s)] for s in shape]
    for i, dim in enumerate(shape):
        if len(labels[i]) == dim == 1:
            new_labels[i] = labels[i]
            continue
        for j in range(dim):
            row = np.take(out, j, axis=i).reshape(-1, len(labels))
            common = _longest_common_substring(tuple(map(tuple, row.tolist())))
            if len(common) == 0:
                logging.warn(f"Could not find common substring for "
                             f"labels dimension {i} index {j}")
                common = sorted(list(set(d[i] for d in row)),
                                key=labels[i].index)
            new_labels[i][j] = delim.join(common)
        for t in (t for t in set(types) if t is not str):
            try:
                new_labels[i] = list(map(t, new_labels[i]))
            except ValueError:
                pass
    return list(map(tuple, new_labels))


def _longest_common_substring(strings: tuple[tuple[str]]) -> tuple[str]:
    matrix = [[] for _ in range(len(strings))]
    for i in range(len(strings) - 1):
        matrix[i] = _lcs(strings[i], strings[i + 1])
    else:
        matrix[-1] = [True for _ in range(len(strings[-1]))]
    return np.array(strings[0])[np.all(matrix, axis=0)].tolist()


@functools.lru_cache(None)
def _lcs(s1: tuple, s2: tuple) -> list[bool]:
    matrix = [False for _ in range(len(s1))]
    for i in range(len(s1)):
        if s1[i] == s2[i]:
            matrix[i] = True
    return matrix


def add_to_list_if_not_present(lst: list, element: Iterable):
    """Add an element to a list if it is not present. Runs in O(1) time.

    Parameters
    ----------
    lst : list
        The list to add the element to.
    element : Iterable
        The element to add to the list.

    References
    ----------
    [1] https://www.youtube.com/watch?v=PXWL_Xzyrp4

    Examples
    --------
    >>> lst = [1, 2, 3]
    >>> add_to_list_if_not_present(lst, [3, 4, 5])
    >>> lst
    [1, 2, 3, 4, 5]
    """
    seen = set(lst)
    lst.extend(x for x in element if not (x in seen or seen.add(x)))


def inner_all_keys(data: dict, keys: list = None, lvl: int = 0):
    """Get all keys of a nested dictionary.

    Parameters
    ----------
    data : dict
        The nested dictionary to get the keys of.
    keys : list, optional
        The list of keys, by default None
    lvl : int, optional
        The level of the dictionary, by default 0

    Returns
    -------
    tuple
        The tuple of keys.

    Examples
    --------
    >>> data = {'a': {'b': {'c': 1}}}
    >>> inner_all_keys(data)
    (('a',), ('b',), ('c',))
    >>> data = {'a': {'b': {'c': 1}}, 'd': {'b': {'c': 2, 'e': 3}}}
    >>> inner_all_keys(data)
    (('a', 'd'), ('b',), ('c', 'e'))
    """
    if keys is None:
        keys = []
    if np.isscalar(data):
        return
    elif isinstance(data, dict):
        if len(keys) < lvl + 1:
            keys.append(list(data.keys()))
        else:
            add_to_list_if_not_present(keys[lvl], data.keys())
        for d in data.values():
            inner_all_keys(d, keys, lvl+1)
    elif isinstance(data, np.ndarray):
        data = np.atleast_1d(data)
        rows = range(data.shape[0])
        if len(keys) < lvl+1:
            keys.append(list(rows))
        else:
            add_to_list_if_not_present(keys[lvl], rows)
        if len(data.shape) > 1:
            inner_all_keys(data[0], keys, lvl+1)
    else:
        raise TypeError(f"Unexpected data type: {type(data)}")
    return tuple(map(tuple, keys))


def combine_arrays(*arrays, delim: str = '-') -> np.ndarray:
    # Create a meshgrid of indices
    grids = np.meshgrid(*arrays, indexing='ij')

    # Combine the grids into a single array with string concatenation
    result = np.core.defchararray.add(grids[0], delim)
    for grid in grids[1:]:
        result = np.core.defchararray.add(result, grid)

    return result


def inner_array(data: dict | np.ndarray) -> np.ndarray | None:
    """Convert a nested dictionary to a nested array.

    Parameters
    ----------
    data : dict or np.ndarray
        The nested dictionary to convert.

    Returns
    -------
    np.ndarray or None
        The converted nested array.

    Examples
    --------
    >>> data = {'a': {'b': {'c': 1}}}
    >>> inner_array(data)
    array([[[1.]]])
    >>> data = {'a': {'b': {'c': 1}}, 'd': {'b': {'c': 2, 'e': 3}}}
    >>> inner_array(data)
    array([[[ 1., nan]],
    <BLANKLINE>
           [[ 2.,  3.]]])
    """
    if np.isscalar(data):
        return data
    elif isinstance(data, dict):
        gen_arr = (inner_array(d) for d in data.values())
        arr = [a for a in gen_arr if a is not None]
        if len(arr) > 0:
            return concatenate_arrays(arr, axis=None)
    # elif not isinstance(data, np.ndarray):
    #     raise TypeError(f"Unexpected data type: {type(data)}")

    # Call np.atleast_1d once and store the result in a variable
    data_1d = np.atleast_1d(data)

    # Use the stored result to check the length of data
    if len(data_1d) == 0:
        return
    elif len(data_1d) == 1:
        return data
    else:
        return np.array(data)


def inner_dict(data: np.ndarray) -> dict | None:
    """Convert a nested array to a nested dictionary.

    Parameters
    ----------
    data : np.ndarray
        The nested array to convert.

    Returns
    -------
    dict or None
        The converted nested dictionary.

    Examples
    --------
    >>> data = np.array([[[1]]])
    >>> inner_dict(data)
    {0: {0: {0: 1}}}
    >>> data = np.array([[[1, np.nan]],
    ...                  [[2, 3]]])
    >>> inner_dict(data)
    {0: {0: {0: 1.0, 1: nan}}, 1: {0: {0: 2.0, 1: 3.0}}}
    """
    if np.isscalar(data):
        return data
    elif len(data) == 0:
        return
    elif isinstance(data, np.ndarray):
        return {i: inner_dict(d) for i, d in enumerate(data)}
    else:
        return data


def combine(data: dict, levels: tuple[int, int], delim: str = '-') -> dict:
    """Combine any levels of a nested dict into the lower level

    Takes the input nested dict and rearranges the top and bottom
    sub-dictionary.

    Parameters
    data: dict
        The nested dict to combine
    levels: tuple[int, int]
        The levels to combine, e.g. (0, 1) will combine the 1st and 2nd level
        of the dict keys into one level at the 2nd level.

    Returns
    dict
        The combined dict

    Examples
    >>> data = {'a': {'b': {'c': 1}}}
    >>> combine(data, (0, 2))
    {'b': {'a-c': 1}}

    >>> data = {'a': {'b': {'c': 1}}, 'd': {'b': {'c': 2, 'e': 3}}}
    >>> combine(data, (0, 2))
    {'b': {'a-c': 1, 'd-c': 2, 'd-e': 3}}
    """

    assert levels[0] >= 0, "first level must be >= 0"
    assert levels[1] > levels[0], "second level must be > first level"

    def _combine_helper(data, levels, depth, keys):
        if depth == levels[1]:
            return {f'{keys[levels[0]]}{delim}{k}': v for k, v in data.items()}
        elif depth == levels[0]:
            new_dict = {}
            for k, v in data.items():
                for k2, v2 in _combine_helper(v, levels, depth + 1,
                                              keys + [k]).items():
                    if isinstance(v2, dict):
                        if k2 in new_dict:
                            new_dict[k2] = _merge(new_dict[k2], v2)
                        else:
                            new_dict[k2] = v2
                    else:
                        new_dict[k2] = v2
            return new_dict
        else:
            return {k: _combine_helper(v, levels, depth + 1, keys + [k]) for
                    k, v in data.items()}

    def _merge(d1: dict, d2: dict) -> dict:
        for k, v in d2.items():
            if isinstance(v, dict):
                d1[k] = _merge(d1.get(k, {}), v)
            else:
                d1[k] = v
        return d1

    result = _combine_helper(data, levels, 0, [])

    return result


def get_elbow(data: np.ndarray) -> int:
    """Draws a line between the first and last points in a dataset and finds
    the point furthest from that line.

    Parameters
    ----------
    data : array
        The data to find the elbow in.

    Returns
    -------
    int
        The index of the elbow point.

    Examples
    --------
    >>> data = np.array([0, 1, 2, 3, 4, 4.5, 5, 5.5, 6, 7, 8, 9, 10])
    >>> get_elbow(data)
    4
    >>> data = np.array([1, 2, 3, 4, 5, 4.5, 4, 3.5, 3, 2, 1])
    >>> get_elbow(data)
    4
    """
    nPoints = len(data)
    allCoord = np.vstack((range(nPoints), data)).T
    np.array([range(nPoints), data])
    firstPoint = allCoord[0]
    lineVec = allCoord[-1] - allCoord[0]
    lineVecNorm = lineVec / np.sqrt(np.sum(lineVec ** 2))
    vecFromFirst = allCoord - firstPoint
    scalarProduct = np.sum(vecFromFirst * repmat(
        lineVecNorm, nPoints, 1), axis=1)
    vecFromFirstParallel = np.outer(scalarProduct, lineVecNorm)
    vecToLine = vecFromFirst - vecFromFirstParallel
    distToLine = np.sqrt(np.sum(vecToLine ** 2, axis=1))
    # set distance to points below lineVec to 0
    distToLine[vecToLine[:, 1] < 0] = 0
    idxOfBestPoint = np.argmax(distToLine)
    return idxOfBestPoint


def events_in_order(inst: mne.BaseEpochs) -> list[str]:
    ids = {v: k for k, v in inst.event_id.items()}
    return [ids[e[2]] for e in inst.events]


if __name__ == "__main__":
    import os
    from ieeg.io import get_data
    import mne
    conds = {"resp": ((-1, 1), "Response/LS"), "aud_ls": ((-0.5, 1.5), "Audio/LS"),
             "aud_lm": ((-0.5, 1.5), "Audio/LM"), "aud_jl": ((-0.5, 1.5), "Audio/JL"),
             "go_ls": ((-0.5, 1.5), "Go/LS"), "go_lm": ((-0.5, 1.5), "Go/LM"),
             "go_jl": ((-0.5, 1.5), "Go/JL")}
    task = "SentenceRep"
    root = os.path.expanduser("~/Box/CoganLab")
    layout = get_data(task, root=root)
    folder = 'stats_old'
    mne.set_log_level("ERROR")

    arr = np.arange(24).reshape((2, 3, 4))
    labels = (('a', 'b'), ('c', 'd', 'e'), ('f', 'g', 'h', 'i'))
    ad = LabeledArray(arr, labels)
    Labels(['a', 'b', 'c']) @ Labels(['d', 'e', 'f'])

    labels = Labels(np.arange(1000))
    l2d = labels @ labels
    x = l2d.reshape((10, -1)).decompose()

import numpy as np
from ieeg.calc._fast.ufuncs import mean_diff as _md, t_test as _ttest
from ieeg.calc._fast.mixup import mixupnd as cmixup, normnd as cnorm
from ieeg.calc._fast.permgt import permgtnd as permgt
from ieeg.calc._fast.concat import nan_concatinate
from scipy.stats import ttest_ind, rankdata
from functools import partial

__all__ = ["mean_diff", "mixup", "permgt", "norm", "concatenate_arrays",
           "ttest", "brunnermunzel"]


def brunnermunzel(x: np.ndarray, y: np.ndarray, axis=None, nan_policy='omit'):
    """Compute the Brunner-Munzel test on samples x and y.

    The Brunner-Munzel test is a nonparametric test of the null hypothesis that
    when values are taken one by one from each group, the probabilities of
    getting large values in both groups are equal.
    Unlike the Wilcoxon-Mann-Whitney's U test, this does not require the
    assumption of equivariance of two groups. Note that this does not assume
    the distributions are same. This test works on two independent samples,
    which may have different sizes.

    Parameters
    ----------
    x, y : array_like
        Array of samples, should be one-dimensional.
    alternative : {'two-sided', 'less', 'greater'}, optional
        Defines the alternative hypothesis.
        The following options are available (default is 'two-sided'):

          * 'two-sided'
          * 'less': one-sided
          * 'greater': one-sided
    distribution : {'t', 'normal'}, optional
        Defines how to get the p-value.
        The following options are available (default is 't'):

          * 't': get the p-value by t-distribution
          * 'normal': get the p-value by standard normal distribution.
    nan_policy : {'propagate', 'raise', 'omit'}, optional
        Defines how to handle when input contains nan.
        The following options are available (default is 'propagate'):

          * 'propagate': returns nan
          * 'raise': throws an error
          * 'omit': performs the calculations ignoring nan values

    Returns
    -------
    statistic : float
        The Brunner-Munzer W statistic.
    pvalue : float
        p-value assuming an t distribution. One-sided or
        two-sided, depending on the choice of `alternative` and `distribution`.

    See Also
    --------
    mannwhitneyu : Mann-Whitney rank test on two samples.

    Notes
    -----
    Brunner and Munzel recommended to estimate the p-value by t-distribution
    when the size of data is 50 or less. If the size is lower than 10, it would
    be better to use permuted Brunner Munzel test (see [2]_).

    References
    ----------
    .. [1] Brunner, E. and Munzel, U. "The nonparametric Benhrens-Fisher
           problem: Asymptotic theory and a small-sample approximation".
           Biometrical Journal. Vol. 42(2000): 17-25.
    .. [2] Neubert, K. and Brunner, E. "A studentized permutation test for the
           non-parametric Behrens-Fisher problem". Computational Statistics and
           Data Analysis. Vol. 51(2007): 5192-5204.

    Examples
    --------
    >>> from scipy.stats import brunnermunzel as bz
    >>> x1 = np.array([1,2,1,1,1,1,1,1,1,1,2,4,1,1])
    >>> x2 = np.array([3,3,4,3,1,2,3,1,1,5,4])
    >>> brunnermunzel(x1, x2), bz(x1, x2, alternative='greater').statistic
    3.1374674823029505
    >>> x3 = np.array([[1,2,1,1],[1,1,1,1],[1,1,2,4]])
    >>> x4 = np.array([[3,3,4,3],[1,2,3,1], [1,5,4,4]])
    >>> brunnermunzel(x3, x4, axis=0), bz(x3, x4, axis=0, alternative='greater').statistic
    3.1374674823029505
    >>> brunnermunzel(x3, x4, axis=1), bz(x3, x4, axis=1, alternative='greater').statistic
    >>> brunnermunzel(x3, x4, axis=None), bz(x3, x4, axis=None, alternative='greater').statistic
    """

    if axis is None:
        nx, ny = x.size, y.size
        idxx = slice(0, nx)
        idxy = slice(nx, nx+ny)
        x, y = x.flat, y.flat
        concat = np.concatenate((x, y), axis=0)
    else:
        while axis < 0:
            axis += x.ndim
        nx, ny = x.shape[axis], y.shape[axis]
        idxx = tuple(slice(None) if i != axis else slice(0, nx)
                 for i in range(x.ndim))
        idxy = tuple(slice(None) if i != axis else slice(nx, nx+ny)
                    for i in range(x.ndim))
        concat = np.concatenate((x, y), axis=axis)

    where = ~np.isnan(concat)
    if nan_policy == 'omit':
        rank = partial(rankdata, nan_policy=nan_policy)
        wherex, wherey = where[idxx], where[idxy]
    else:
        rank = rankdata
        wherex = wherey = None
        if np.any(~where) and nan_policy == 'raise':
            raise ValueError("The input contains NaN.")

    kwargsx = dict(axis=axis, where=wherex, keepdims=True)
    kwargsy = dict(axis=axis, where=wherey, keepdims=True)

    rankc = rank(concat, axis=axis)
    rankcx, rankcy = rankc[idxx], rankc[idxy]
    rankcx_mean, rankcy_mean = rankcx.mean(**kwargsx), rankcy.mean(**kwargsy)
    rankx, ranky = rank(x, axis=axis), rank(y, axis=axis)
    rankx_mean, ranky_mean = rankx.mean(**kwargsx), ranky.mean(**kwargsy)

    Sx = np.sum(np.power(rankcx - rankx - rankcx_mean + rankx_mean, 2.0),
                **kwargsx) / (nx - 1)
    Sy = np.sum(np.power(rankcy - ranky - rankcy_mean + ranky_mean, 2.0),
                **kwargsy) / (ny - 1)

    wbfn = nx * ny * (rankcy_mean - rankcx_mean)
    wbfn /= (nx + ny) * np.sqrt(nx * Sx + ny * Sy)
    return np.squeeze(wbfn)

def ttest(group1: np.ndarray, group2: np.ndarray,
          axis: int) -> np.ndarray:
    """Calculate the t-statistic between two groups.

    This function is the default statistic function for time_perm_cluster. It
    calculates the t-statistic between two groups along the specified axis.

    Parameters
    ----------
    group1 : array, shape (..., time)
        The first group of observations.
    group2 : array, shape (..., time)
        The second group of observations.
    axis : int or tuple of ints, optional
        The axis or axes along which to compute the t-statistic. If None,
        compute the t-statistic over all axes.

    Returns
    -------
    t : array
        The t-statistic between the two groups.

    Examples
    --------
    >>> import numpy as np
    >>> group1 = np.array([[1, 1, 1, 1, 1], [0, 60, 0, 10, 0]])
    >>> group2 = np.array([[1, 1, 1, 1, 1], [0, 0, 0, 0, 0]])
    >>> ttest(group1, group2, 1)
    array([      nan, 1.2004901])
    >>> ttest(group1, group2, 0)
    array([0.        , 1.01680311, 0.        , 1.10431526, 0.        ])
    >>> group3 = np.arange(100000, dtype=float).reshape(20000, 5)
    >>> ttest(group3, group1, 0)
    array([244.92741947, 242.26926888, 244.93721715, 244.858866  ,
           244.94701484])
    """
    return _ttest(group1, group2, axes=[axis, axis])


def concatenate_arrays(arrays: tuple[np.ndarray, ...], axis: int = 0
                       ) -> np.ndarray:
    """Concatenate arrays along a specified axis, filling in empty arrays with
    nan values.

    Parameters
    ----------
    arrays
        A list of arrays to concatenate
    axis
        The axis along which to concatenate the arrays

    Returns
    -------
    result
        The concatenated arrays

    Examples
    --------
    >>> concatenate_arrays((np.array([1, 2, 3]), np.array([4, 5])), axis=None)
    array([[ 1.,  2.,  3.],
           [ 4.,  5., nan]])
    >>> concatenate_arrays((np.array([1, 2, 3]), np.array([4, 5])), axis=0)
    array([1., 2., 3., 4., 5.])
    >>> arr1 = np.arange(6, dtype=float).reshape(1, 2, 3)
    >>> arr2 = np.arange(24, dtype=float).reshape(2, 3, 4)
    >>> concatenate_arrays((arr1[0], arr2[0]), axis=0)
    array([[ 0.,  1.,  2., nan],
           [ 3.,  4.,  5., nan],
           [ 0.,  1.,  2.,  3.],
           [ 4.,  5.,  6.,  7.],
           [ 8.,  9., 10., 11.]])
    >>> concatenate_arrays((arr2[0], arr1[0]), axis=1)
    array([[ 0.,  1.,  2.,  3.,  0.,  1.,  2.],
           [ 4.,  5.,  6.,  7.,  3.,  4.,  5.],
           [ 8.,  9., 10., 11., nan, nan, nan]])
    >>> arr = concatenate_arrays((arr1[0], arr2[0]), axis=None)
    >>> arr
    array([[[ 0.,  1.,  2., nan],
            [ 3.,  4.,  5., nan],
            [nan, nan, nan, nan]],
    <BLANKLINE>
           [[ 0.,  1.,  2.,  3.],
            [ 4.,  5.,  6.,  7.],
            [ 8.,  9., 10., 11.]]])
    """

    if axis is None:
        axis = 0
        arrays = [np.expand_dims(ar, axis) for ar in arrays]

    arrays = [ar.astype(float) for ar in arrays if ar.size > 0]

    while axis < 0:
        axis += max(a.ndim for a in arrays)

    return nan_concatinate(arrays, axis)


def mixup(arr: np.ndarray, obs_axis: int, alpha: float = 1.,
          seed: int = None) -> None:
    """Oversample by mixing two random non-NaN observations

    Parameters
    ----------
    arr : array
        The data to oversample.
    obs_axis : int
        The axis along which to apply func.
    alpha : float
        The alpha parameter for the beta distribution. If alpha is 0, then
        the distribution is uniform. If alpha is 1, then the distribution is
        symmetric. If alpha is greater than 1, then the distribution is
        skewed towards the first observation. If alpha is less than 1, then
        the distribution is skewed towards the second observation.

    Examples
    --------
    >>> arr = np.array([[1, 2], [4, 5], [7, 8],
    ... [float("nan"), float("nan")]])
    >>> mixup(arr, 0, seed=42)
    >>> arr # doctest: +NORMALIZE_WHITESPACE +SKIP
    array([[1.        , 2.        ],
           [4.        , 5.        ],
           [7.        , 8.        ],
           [5.24946679, 6.24946679]])
    >>> arr2 = np.arange(24, dtype=float).reshape(2, 3, 4)
    >>> arr2[0, 2, :] = [float("nan")] * 4
    >>> mixup(arr2, 1, seed=42)
    >>> arr2 # doctest: +NORMALIZE_WHITESPACE +SKIP
    array([[[ 0.        ,  1.        ,  2.        ,  3.        ],
            [ 4.        ,  5.        ,  6.        ,  7.        ],
            [ 2.33404428,  3.33404428,  4.33404428,  5.33404428]],
    <BLANKLINE>
           [[12.        , 13.        , 14.        , 15.        ],
            [16.        , 17.        , 18.        , 19.        ],
            [20.        , 21.        , 22.        , 23.        ]]])
    >>> arr3 = np.arange(24, dtype=float).reshape(3, 2, 4)
    >>> arr3[0, :, :] = float("nan")
    >>> mixup(arr3, 0, seed=42)
    >>> arr3 # doctest: +NORMALIZE_WHITESPACE +SKIP
    array([[[12.66808855, 13.66808855, 14.66808855, 15.66808855],
            [17.31717879, 18.31717879, 19.31717879, 20.31717879]],
    <BLANKLINE>
           [[ 8.        ,  9.        , 10.        , 11.        ],
            [12.        , 13.        , 14.        , 15.        ]],
    <BLANKLINE>
           [[16.        , 17.        , 18.        , 19.        ],
            [20.        , 21.        , 22.        , 23.        ]]])
    """

    if arr.ndim > 3:
        for i in range(arr.shape[0]):
            mixup(arr[i], obs_axis - 1, alpha, seed)
    elif arr.ndim == 1:
        raise ValueError("Array must have at least 2 dimensions")
    else:
        if seed is None:
            seed = np.random.randint(0, 2 ** 16 - 1)
        if obs_axis == 0:
            arr = arr.swapaxes(1, obs_axis)
        cmixup(arr, 1, alpha, seed)


def norm(arr: np.ndarray, obs_axis: int = -1) -> None:
    """Oversample by obtaining the distribution and randomly selecting

    Parameters
    ----------
    arr : array
        The data to oversample.
    obs_axis : int
        The axis along which to apply func.

    Examples
    --------
    >>> np.random.seed(0)
    >>> arr = np.array([1, 2, 4, 5, 7, 8,
    ... float("nan"), float("nan")])
    >>> norm(arr)
    >>> arr
    array([1.        , 2.        , 4.        , 5.        , 7.        ,
           8.        , 8.91013086, 5.50039302])
    """
    cnorm(arr, obs_axis)


def mean_diff(group1: np.ndarray, group2: np.ndarray,
              axis: int = -1) -> np.ndarray | float:
    """Calculate the mean difference between two groups.

    This function is the default statistic function for time_perm_cluster. It
    calculates the mean difference between two groups along the specified axis.

    Parameters
    ----------
    group1 : array, shape (..., time)
        The first group of observations.
    group2 : array, shape (..., time)
        The second group of observations.
    axis : int or tuple of ints, optional
        The axis or axes along which to compute the mean difference. If None,
        compute the mean difference over all axes.

    Returns
    -------
    avg1 - avg2 : array or float
        The mean difference between the two groups.

    Examples
    --------
    >>> import numpy as np
    >>> group1 = np.array([[1, 1, 1, 1, 1], [0, 60, 0, 10, 0]])
    >>> group2 = np.array([[1, 1, 1, 1, 1], [0, 0, 0, 0, 0]])
    >>> mean_diff(group1, group2, axis=1)
    array([ 0., 14.])
    >>> mean_diff(group1, group2, axis=0)
    array([ 0., 30.,  0.,  5.,  0.])
    >>> group3 = np.arange(100000, dtype=float).reshape(20000, 5)
    >>> mean_diff(group3, group1, axis=0)
    array([49997., 49968., 49999., 49995., 50001.])
    """
    return _md(group1, group2, axes=[axis, axis])


if __name__ == "__main__":
    import numpy as np
    from timeit import timeit

    np.random.seed(0)
    n = 1000
    group1 = np.random.rand(100, 100, 100)
    group2 = np.random.rand(500, 100, 100)

    kwargs = dict(globals=globals(), number=n)
    time1 = timeit('mean_diff(group1, group2, axis=0)', **kwargs)
    # time2 = timeit('_md(group1, group2, axes=[0, 0])', **kwargs)

    print(f"mean_diff: {time1 / n:.3g} per run")
    # print(f"md: {time2 / n:.3g} per run")
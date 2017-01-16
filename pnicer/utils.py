# -----------------------------------------------------------------------------
# Import stuff
import os
import sys
import importlib
import numpy as np
import multiprocessing

from astropy import wcs
from astropy.io import fits
from scipy.integrate import cumtrapz
from multiprocessing.pool import Pool
from itertools import combinations, repeat
# noinspection PyPackageRequirements
from sklearn.neighbors import KernelDensity
# noinspection PyPackageRequirements
from sklearn.mixture.gaussian_mixture import GaussianMixture


# -----------------------------------------------------------------------------
# Useful constants
std2fwhm = 2 * np.sqrt(2 * np.log(2))


# -----------------------------------------------------------------------------
def distance_sky(lon1, lat1, lon2, lat2, unit="radians"):
    """
    Returns the distance between two objects on a sphere along the connecting great circle. Also works with arrays.

    Parameters
    ----------
    lon1 : int, float, np.ndarray
        Longitude (e.g. Right Ascension) of first object
    lat1 : int, float, np.ndarray
        Latitude (e.g. Declination) of first object
    lon2 : int, float, np.ndarray
        Longitude of object to calculate the distance to.
    lat2 : int, float, np.ndarray
        Longitude of object to calculate the distance to.
    unit : str, optional
        The unit in which the coordinates are given. Either 'radians' or 'degrees'. Default is 'radians'. Output will
        be in the same units.

    Returns
    -------
    float, np.ndarray
        On-sky distances between given objects.

    """

    l1, l2 = np.radians(lon1) if "deg" in unit else lon1, np.radians(lon2) if "deg" in unit else lon2
    b1, b2 = np.radians(lat1) if "deg" in unit else lat1, np.radians(lat2) if "deg" in unit else lat2

    # Return haversine distance
    dis = 2 * np.arcsin(np.sqrt(np.sin((b1 - b2) / 2.) ** 2 + np.cos(b1) * np.cos(b2) * np.sin((l1 - l2) / 2.) ** 2))
    if "deg" in unit:
        return np.rad2deg(dis)
    else:
        return dis


# -----------------------------------------------------------------------------
def weighted_avg(values, weights):
    """
    Calculates weighted mean and standard deviation.

    Parameters
    ----------
    values : np.ndarray
        Data values.
    weights : np.ndarray
        Weights for each data point.

    Returns
    -------
    tuple(np.ndarray, np.ndarray)
        Weighted mean and variance.

    """

    # Calculate weighted average
    average = np.nansum(values * weights) / np.nansum(weights)

    # Calculate weighted variance
    variance = np.nansum((values - average) ** 2 * weights) / np.nansum(weights)

    # Return both
    return average, variance


# -----------------------------------------------------------------------------
def flatten_lol(lst):
    """
    Flattens a list of lists.

    Parameters
    ----------
    lst : list
        Input list (that contains lists).

    Returns
    -------
    iterable
        Flattened single list.

    """
    return [item for sublist in lst for item in sublist]


# -----------------------------------------------------------------------------
def get_sample_covar(xi, yi):
    """
    Calculate sample covariance (can not contain NaNs!).

    Parameters
    ----------
    xi : np.ndarray
        X data.
    yi : np.ndarray
        Y data.

    Returns
    -------
    float
        Sample covariance.

    """

    # Sample size must be equal
    if len(xi) != len(yi):
        raise ValueError("X and Y sample size must be equal.")

    # Check for NaNs
    if (np.sum(~np.isfinite(xi)) > 0) | (np.sum(~np.isfinite(yi)) > 0):
        raise ValueError("Sample contains NaN entries")

    return np.sum((xi - np.mean(xi)) * (yi - np.mean(yi))) / len(xi)


# -----------------------------------------------------------------------------
def get_color_covar(magerr1, magerr2, magerr3, magerr4, name1, name2, name3, name4):
    """
    Calculate the error covariance matrix for color combinations of four magnitudes.
    x: (mag1 - mag2)
    y: (mag3 - mag4)

    Parameters
    ----------
    magerr1 : np.ndarray
        Source magnitudes in band 1.
    magerr2 : np.ndarray
        Source magnitudes in band 2.
    magerr3 : np.ndarray
        Source magnitudes in band 3.
    magerr4 : np.ndarray
        Source magnitudes in band 4.
    name1 : str, int
        Unique identifier string or index for band 1.
    name2 : str, int
        Unique identifier string or index for band 2.
    name3 : str, int
        Unique identifier string or index for band 3.
    name4 : str, int
        Unique identifier string or index for band 4.

    Returns
    -------
    np.ndarray
        Covariance matrix for color errors.

    """

    # Initialize matrix
    cmatrix = np.zeros(shape=(2, 2))

    # Calculate first entry
    cmatrix[0, 0] = np.mean(magerr1) ** 2 + np.mean(magerr2) ** 2

    # Calculate last entry
    cmatrix[1, 1] = np.mean(magerr3) ** 2 * np.mean(magerr4) ** 2

    # Initially set cross entries to 0
    cov = 0.

    # Add first term
    if name1 == name3:
        cov += np.mean(magerr1) * np.mean(magerr3)

    # Add second term
    if name1 == name4:
        cov -= np.mean(magerr1) * np.mean(magerr4)

    # Add third term
    if name2 == name3:
        cov -= np.mean(magerr2) * np.mean(magerr3)

    # Add fourth term
    if name2 == name4:
        cov += np.mean(magerr2) * np.mean(magerr4)

    # Set entries in matrix
    cmatrix[1, 0] = cmatrix[0, 1] = cov

    # Return total covariance
    return cmatrix


# -----------------------------------------------------------------------------
def round_partial(data, precision):
    """
    Simple static method to round data to arbitrary precision.

    Parameters
    ----------
    data : float, np.ndarray
        Data to be rounded.
    precision : float, np.ndarray
        Desired precision. e.g. 0.2.

    Returns
    -------
    float, np.ndarray
        Rounded data.

    """

    return np.around(data / precision) * precision


# -----------------------------------------------------------------------------
def caxes(ndim, ax_size=None, labels=None):
    """
    Creates a grid of axes to plot all combinations of data.

    Parameters
    ----------
    ndim : int
        Number of dimensions.
    ax_size : list, optional
        Single axis size. Default is [3, 3].
    labels : iterable, optional
        Optional list of feature names

    Returns
    -------
    tuple
        tuple containing the figure and a list of the axes.

    """

    # import
    from matplotlib import pyplot as plt

    if labels is not None:
        if len(labels) != ndim:
            raise ValueError("Number of provided labels must match dimensions")

    if ax_size is None:
        ax_size = [3, 3]

    # Get all combinations to plot
    c = combinations(range(ndim), 2)

    # Create basic plot layout
    fig, axes = plt.subplots(ncols=ndim - 1, nrows=ndim - 1, figsize=[(ndim - 1) * ax_size[0], (ndim - 1) * ax_size[1]])

    if ndim == 2:
        axes = np.array([[axes], ])

    # Adjust plots
    plt.subplots_adjust(left=0.1, bottom=0.1, right=0.95, top=0.95, wspace=0, hspace=0)

    axes_out = []
    for idx in c:

        # Get index of subplot
        x_idx, y_idx = ndim - idx[0] - 2, ndim - idx[1] - 1

        # Grab axis
        ax = axes[x_idx, y_idx]

        # Hide tick labels
        if x_idx < ndim - 2:
            ax.axes.xaxis.set_ticklabels([])
        if y_idx > 0:
            ax.axes.yaxis.set_ticklabels([])

        # Add axis labels
        if labels is not None:
            if ax.get_position().x0 < 0.11:
                ax.set_ylabel("$" + labels[idx[0]] + "$")
            if ax.get_position().y0 < 0.11:
                ax.set_xlabel("$" + labels[idx[1]] + "$")

        # Append axes to return list
        axes_out.append(axes[x_idx, y_idx])

        # Delete not necessary axes
        if ((idx[0] > 0) | (idx[1] - 1 > 0)) & (idx[0] != idx[1] - 1):
            fig.delaxes(axes[idx[0], idx[1] - 1])

    return fig, axes_out


# -----------------------------------------------------------------------------
def caxes_delete_ticklabels(axes, xfirst=False, xlast=False, yfirst=False, ylast=False):
    """
    Deletes tick labels from a combination axes list.

    Parameters
    ----------
    axes : iterable
        The combination axes list.
    xfirst : bool, optional
        Whether the first x label should be deleted.
    xlast : bool, optional
        Whether the last x label should be deleted.
    yfirst : bool, optional
        Whether the first y label should be deleted.
    ylast : bool, optional
        Whether the last y label should be deleted.


    """

    # Loop through the axes
    for ax, idx in zip(axes, combinations(range(len(axes)), 2)):

        # Modify x ticks
        if idx[0] == 0:

            # Grab ticks
            xticks = ax.xaxis.get_major_ticks()

            # Conditionally delete
            if xfirst:
                xticks[0].set_visible(False)
            if xlast:
                xticks[-1].set_visible(False)

        if idx[1] == np.max(idx):

            # Grab ticks
            yticks = ax.yaxis.get_major_ticks()

            # Conditionally delete
            if yfirst:
                yticks[0].set_visible(False)
            if ylast:
                yticks[-1].set_visible(False)


# -----------------------------------------------------------------------------
def mp_kde(grid, data, bandwidth, kernel="epanechnikov", norm=None, absolute=False, sampling=None):
    """
    Kernel density estimation with parallelisation.

    Parameters
    ----------
    grid
        Grid on which to evaluate the density.
    data
        Input data
    bandwidth : int, float
        Bandwidth of kernel (in data units).
    kernel : str, optional
        Name of kernel for KDE. e.g. 'epanechnikov' or 'gaussian'. Default is 'epanechnikov'.
    norm : str, optional
        Whether to normalize the result (density estimate from 0 to 1). Default is False.
    absolute : bool, optional
        Whether to return absolute numbers.
    sampling : int, optional
        Sampling of grid. Necessary only when absolute numbers should be returned.

    Returns
    -------
    np.ndarray

    """

    # If we want absolute values, we must specify the sampling
    if absolute:
        if not sampling:
            raise ValueError("For absolute values, sampling needs to be specified")

    # Dimensions of grid and data must match
    if len(grid.shape) != len(data.shape):
        raise ValueError("Data and Grid dimensions must match")

    # If only one dimension, extend
    if len(grid.shape) == 1:
        grid = grid[:, np.newaxis]
        data = data[:, np.newaxis]

    # Split for parallel processing
    grid_split = np.array_split(grid, multiprocessing.cpu_count(), axis=0)

    # Define kernel
    kde = KernelDensity(kernel=kernel, bandwidth=bandwidth)

    # Run kernel density calculation
    with Pool() as pool:
        mp = pool.starmap(_mp_kde, zip(repeat(kde), repeat(data), grid_split))

    # Create array
    mp = np.concatenate(mp)

    # If we want absolute numbers we have to evaluate the same thing for the grid
    if absolute:
        mp *= data.shape[0] / np.sum(mp) * sampling

    # Normalize if set
    if norm == "max":
        mp /= np.nanmax(mp)
    elif norm == "mean":
        mp /= np.nanmean(mp)
    elif norm == "sum":
        mp /= np.nansum(mp)

    # Return
    return mp


# -----------------------------------------------------------------------------
def _mp_kde(kde, data, grid):
    """
    Parallelisation routine for kernel density estimation.

    Parameters
    ----------
    kde
        KernelDensity instance from scikit learn
    data
        Input data
    grid
        Grid on which to evaluate the density.

    Returns
    -------
    np.ndarray

    """

    return np.exp(kde.fit(data).score_samples(grid))


# -----------------------------------------------------------------------------
def mp_gmm(data, **kwargs):
    """
    Gaussian mixture model fitting with parallelisation. The parallelisation only works when mutliple sets need to be
    fit.

    Parameters
    ----------
    data : iterable
        Iterable (list) of data vectors to be fit
    kwargs
        Additional keyword arguments for GaussianMixture class.

    Returns
    -------
    iterable
        List of results of fitting.

    """

    # Determine gaussian mixture model and return
    with Pool() as pool:
        return pool.starmap(_mp_gmm, zip(data, repeat(kwargs)))


# -----------------------------------------------------------------------------
def _mp_gmm(data, kwargs):
    """
    Gaussian mixture model fitting helper routine.

    Parameters
    ----------
    data : np.array
        Data to be fit.
    kwargs
        Additional keyword arguments for GaussianMixture class.

    Returns
    -------
        GaussianMixture class or NaN in case the fitting procedure did not converge or was not possible.
    """

    try:

        # Fit model
        # TODO: Force warm_start to be false?!?
        gmm = GaussianMixture(**kwargs).fit(X=data)

        # Check for convergence and return
        if gmm.converged_:
            return gmm
        else:
            return np.nan

    # On error also return NaN
    except ValueError:
        return np.nan


# -----------------------------------------------------------------------------
def get_resource_path(package, resource):
    """
    Returns the path to an included resource.

    Parameters
    ----------
    package : str
        package name (e.g. astropype.resources.sextractor).
    resource : str
        Name of the resource (e.g. default.conv)

    Returns
    -------
    str
        Path to resource.

    """

    # Import package
    importlib.import_module(name=package)

    # Return path to resource
    return os.path.join(os.path.dirname(sys.modules[package].__file__), resource)


# -----------------------------------------------------------------------------
def centroid_sphere(lon, lat, units="radian"):
    """
    Calcualte the centroid on a sphere. Strictly valid only for a unit sphere and for a coordinate system with latitudes
    from -90 to 90 degrees and longitudes from 0 to 360 degrees.

    Parameters
    ----------
    lon : list, np.array
        Input longitudes
    lat : list, np.array
        Input latitudes
    units : str, optional
        Input units. Either 'radian' or 'degree'. Default is 'radian'.

    Returns
    -------
    tuple
        Tuple with (lon, lat) of centroid

    """

    # Convert to radians if degrees
    if "deg" in units.lower():
        mlon, mlat = np.radians(lon), np.radians(lat)
    else:
        mlon, mlat = lon, lat

    # Convert to cartesian coordinates
    x, y, z = np.cos(mlat) * np.cos(mlon), np.cos(mlat) * np.sin(mlon), np.sin(mlat)

    # 3D centroid
    xcen, ycen, zcen = np.sum(x) / len(x), np.sum(y) / len(y), np.sum(z) / len(z)

    # Push centroid to triangle surface
    cenlen = np.sqrt(xcen**2 + ycen**2 + zcen**2)
    xsur, ysur, zsur = xcen / cenlen, ycen / cenlen, zcen / cenlen

    # Convert back to spherical coordinates and return
    outlon = np.arctan2(ysur, xsur)

    # Convert back to 0-2pi range if necessary
    if outlon < 0:
        outlon += 2 * np.pi
    outlat = np.arcsin(zsur)

    # Return
    if "deg" in units.lower():
        return np.degrees(outlon), np.degrees(outlat)
    else:
        return outlon, outlat


# -----------------------------------------------------------------------------
def data2header(lon, lat, frame, proj_code="TAN", pixsize=1/3600, enlarge=1.05, **kwargs):
    """
    Create an astropy Header instance from a given dataset (longitude/latitude). The world coordinate system can be
    chosen between galactic and equatorial; all WCS projections are supported. Very useful for creating a quick WCS
    to plot data.

    Parameters
    ----------
    lon : list, np.array
        Input list or array of longitude coordinates in degrees.
    lat : list, np.array
        Input list or array of latitude coordinates in degrees.
    frame : str, optional
        World coordinate system frame of input data ('icrs' or 'galactic').
    proj_code : str, optional
        Projection code. (e.g. 'TAN', 'AIT', 'CAR', etc). Default is 'TAN'
    pixsize : int, float, optional
        Pixel size of generated header in degrees. Not so important for plots, but still required.
    enlarge : float, optional
        Optional enlargement factor for calculated field size. Default is 1.05. Set to 1 if no enlargement is wanted.
    kwargs
        Additional projection parameters (e.g. pv2_1=-30)

    Returns
    -------
    astropy.fits.Header
        Astropy fits header instance.

    """

    # Define projection
    crval1, crval2 = centroid_sphere(lon=lon, lat=lat, units="degree")

    # Projection code
    if frame.lower() == "icrs":
        ctype1 = "RA{:->6}".format(proj_code)
        ctype2 = "DEC{:->5}".format(proj_code)
        frame = "equ"
    elif frame.lower() == "galactic":
        ctype1 = "GLON{:->4}".format(proj_code)
        ctype2 = "GLAT{:->4}".format(proj_code)
        frame = "gal"
    else:
        raise ValueError("Projection system {0:s} not supported".format(frame))

    # Build additional string
    additional = ""
    for key, value in kwargs.items():
        additional += ("{0: <8}= {1}\n".format(key.upper(), value))

    # Create preliminary header without size information
    header = fits.Header.fromstring("NAXIS   = 2" + "\n"
                                    "CTYPE1  = '" + ctype1 + "'\n"
                                    "CTYPE2  = '" + ctype2 + "'\n"
                                    "CRVAL1  = " + str(crval1) + "\n"
                                    "CRVAL2  = " + str(crval2) + "\n"
                                    "CUNIT1  = 'deg'" + "\n"
                                    "CUNIT2  = 'deg'" + "\n"
                                    "CDELT1  = -" + str(pixsize) + "\n"
                                    "CDELT2  = " + str(pixsize) + "\n"
                                    "COORDSYS= '" + frame + "'" + "\n" +
                                    additional,
                                    sep="\n")

    # Determine extent of data for this projection
    x, y = wcs.WCS(header).wcs_world2pix(lon, lat, 1)
    naxis1 = (np.ceil((x.max()) - np.floor(x.min())) * enlarge).astype(int)
    naxis2 = (np.ceil((y.max()) - np.floor(y.min())) * enlarge).astype(int)

    # Calculate pixel shift relative to centroid (caused by anisotropic distribution of sources)
    xdelta = (x.min() + x.max()) / 2
    ydelta = (y.min() + y.max()) / 2

    # Add size to header
    header["NAXIS1"], header["NAXIS2"] = naxis1, naxis2
    header["CRPIX1"], header["CRPIX2"] = naxis1 / 2 - xdelta, naxis2 / 2 - ydelta

    # Return Header
    return header


# -----------------------------------------------------------------------------
def data2grid(lon, lat, frame, proj_code="TAN", pixsize=5. / 60, **kwargs):
    """
    Method to build a WCS grid with a valid projection given a pixel scale.

    Parameters
    ----------
    lon : list, np.array
        Input list or array of longitude coordinates in degrees.
    lat : list, np.array
        Input list or array of latitude coordinates in degrees.
    frame : str, optional
        World coordinate system frame of input data ('icrs' or 'galactic').
    proj_code : str, optional
        Any WCS projection code (e.g. CAR, TAN, etc.). Default is 'TAN'.
    pixsize : int, float, optional
        Pixel size of grid. Default is 10 arcminutes.
    kwargs
        Additioanl projection parameters if required (e.g. pv2_1=-30, pv2_2=0 for a given COE projection)

    Returns
    -------
    tuple
        Tuple containing the header and the world coordinate grids (lon and lat)

    """

    # Create header from data
    header = data2header(lon=lon, lat=lat, frame=frame, proj_code=proj_code, pixsize=pixsize, **kwargs)

    # Get WCS
    mywcs = wcs.WCS(header=header)

    # Create image coordinate grid
    image_grid = np.meshgrid(np.arange(0, header["NAXIS1"], 1), np.arange(0, header["NAXIS2"], 1))

    # Convert to world coordinates and get WCS grid for this projection
    world_grid = mywcs.wcs_pix2world(image_grid[0], image_grid[1], 0)

    # Return header and grid
    return header, world_grid


# -----------------------------------------------------------------------------
def finalize_plot(path=None):
    """
    Helper method to save or show plot.

    Parameters
    ----------
    path : str, optional
        If set, the path where the figure is saved

    """

    # Import matplotlib
    from matplotlib import pyplot as plt

    # Save or show figure
    if path is None:
        plt.show()
    else:
        plt.savefig(path, bbox_inches='tight')
    plt.close()


# -----------------------------------------------------------------------------
def gmm_scale(gmm, shift=0.0, scale=1.0, reverse=False, params=None):
    """
    Apply scaling factors to GMM instances.

    Parameters
    ----------
    gmm : GaussianMixture
        GMM instance to be scaled.
    shift : int, float, optional
        Shift for the entire model. Default is 0 (no shift).
    scale : int, float, optional
        Scale for all components. Default is 1 (no scale).
    reverse : bool, optional
        Whether the GMM should be reversed.
    params
        GaussianMixture params for initialization of new instance.

    Returns
    -------
    GaussianMixture
        Modified GMM instance.

    """

    # Fetch parameters if not supplied
    if params is None:
        # noinspection PyUnresolvedReferences
        params = gmm.get_params()

    # Instantiate new GMM
    gmm_new = GaussianMixture(params)

    # Create scaled fitted GMM model
    gmm_new.weights_ = gmm.weights_

    # Apply shift if set
    gmm_new.means_ = gmm.means_ + shift

    # Apply scale
    gmm_new.means_ /= scale

    # Reverse if set
    if reverse:
        gmm_new.means_ *= -1

    # gmm_new.means_ = (zp - gmm.means_) / scale
    # TODO: Zero-point must be from rotated data space. Check!
    gmm_new.covariances_ = gmm.covariances_ / scale ** 2
    gmm_new.precisions_ = np.linalg.inv(gmm_new.covariances_)
    gmm_new.precisions_cholesky_ = np.linalg.cholesky(gmm_new.precisions_)

    # Return scaled GMM
    return gmm_new


# -----------------------------------------------------------------------------
def gmm_sample_xy(gmm, kappa=3, sampling=10, nmin=100, nmax=100000):
    """
    Creates discrete values (x, y) for a given GMM instance.

    Parameters
    ----------
    gmm : GaussianMixture
        Fitted GaussianMixture model instance from which to draw samples.
    kappa : int, float
        Width of query range (in units of standard deviations).
    sampling : int
        Sampling factor for the smallest component standard deviation.
    nmin : int
        Minimum number of samples to draw.
    nmax : int
        Maximum number of samples to draw. Not recommended to set this to > 1E5 as it becomes very slow.

    Returns
    -------
    ndarray, ndarray
        Tuple holding x and y data arrays.

    """

    # Get GMM attributes
    s = np.sqrt(gmm.covariances_)
    m = gmm.means_

    # Determine min and max of range
    qmin, qmax = (float(np.min(m) - kappa * np.max(s)), float(np.max(m) + kappa * np.max(s)))

    # Determine number of samples (number of samples from smallest standard deviation with 'sampling' samples)
    nsamples = (qmax - qmin) / (np.min(s) / sampling)

    # Set min/max numer of samples
    nsamples = 100 if nsamples < nmin else nsamples
    nsamples = 100000 if nsamples > nmax else nsamples

    # Get query range
    xrange = np.linspace(start=qmin, stop=qmax, num=nsamples)
    yrange = np.exp(gmm.score_samples(np.expand_dims(xrange, 1)))

    # Step
    return xrange, yrange


# -----------------------------------------------------------------------------
def gmm_max(gmm):
    # TODO: Add docstring
    x, y = gmm_sample_xy(gmm=gmm, kappa=1, sampling=50)
    return x[np.argmax(y)]


# -----------------------------------------------------------------------------
def gmm_expected_value(gmm):
    # TODO: Add docstring (same as mean)
    xrange, yrange = gmm_sample_xy(gmm=gmm, kappa=10, sampling=50)
    return np.trapz(xrange * yrange, xrange)


# -----------------------------------------------------------------------------
def gmm_confidence_interval(gmm, level=0.95):
    """

    Parameters
    ----------
    gmm
    level

    Returns
    -------
    tuple

    """
    # TODO: Modify docstring
    xrange, yrange = gmm_sample_xy(gmm=gmm, kappa=10, sampling=50)

    # Cumulative integral
    cumint = cumtrapz(y=yrange, x=xrange, initial=0)

    # Return interval
    return tuple(np.interp([(1 - level) / 2, level + (1 - level) / 2], cumint, xrange))


# -----------------------------------------------------------------------------
def gmm_population_variance(gmm):
    # TODO: Add docstring

    # Get expected value
    ev = gmm_expected_value(gmm=gmm)

    # Get query range
    xrange, yrange = gmm_sample_xy(gmm=gmm, kappa=10, sampling=50)

    # Return population variance
    return np.trapz(np.power(xrange, 2) * yrange, xrange) - ev**2


# -----------------------------------------------------------------------------
def models_population_variance(self):
    # TODO: Add docstring
    return [self._gmm_population_variance(gmm=gmm) for gmm in self.models]


# -----------------------------------------------------------------------------
def gmm_confidence_interval_value(gmm, value, level=0.95):
    # TODO: Check if this works as intended

    # Get query ranges
    gmm_x, gmm_y = gmm_sample_xy(gmm=gmm, kappa=5, sampling=100, nmin=1000, nmax=100000)

    # Get dx
    dx = np.ediff1d(gmm_x)[0]

    # Find position of 'value'
    value_idx = np.argmin(np.abs(gmm_x - value))

    # Integrate as long as necessary
    for i in range(len(gmm_x)):

        # Current index on both sides
        lidx = value_idx - i if value_idx - i >= 0 else 0
        ridx = value_idx + i if value_idx + i < len(gmm_x) else len(gmm_x) - 1

        # Need to separate left and right integral due to asymmetry
        lint = np.trapz(gmm_y[lidx:value_idx], dx=dx)
        rint = np.trapz(gmm_y[value_idx:ridx], dx=dx)

        # Sum of both sides
        # integral = np.trapz(gmm_y[lidx:ridx], dx=dx)
        integral = lint + rint

        # Break if confidence level reached
        if integral > level:
            break

    # Choose final index for confidence interval
    # noinspection PyUnboundLocalVariable
    ci_half_size = value - gmm_x[lidx] if value_idx - lidx > ridx - value_idx else gmm_x[ridx] - value

    # Return interval
    return value - ci_half_size, value + ci_half_size

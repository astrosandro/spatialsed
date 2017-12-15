from copy import deepcopy
import numpy as np
import os
from prospect.models import priors, sedmodel
from astropy.cosmology import WMAP9 as cosmo
from sedpy.observate import load_filters, getSED

tophat = None

# --------------
# RUN_PARAMS
# --------------
run_params = {'verbose':True,
              'debug':False,
              'outfile':'spatial_demo',
              # dynesty params
              'nested_bound': 'multi', # bounding method
              'nested_sample': 'rwalk', # sampling method
              'nested_walks': 30, # MC walks
              'nested_nlive_batch': 200, # size of live point "batches"
              'nested_nlive_init': 200, # number of initial live points
              'nested_weight_kwargs': {'pfrac': 1.0}, # weight posterior over evidence by 100%
              'nested_dlogz_init': 0.01,
              # Mock data parameters
              'snr': 20.0,
              'add_noise': False,
              # Input mock model parameters
              'mass': np.array([4e10,1e10]),
              'logzsol': np.array([0.0,-0.5]),
              'tage': np.array([12.,4.]),
              'tau': np.array([1.,10]),
              'dust2': np.array([0.2,0.6]),
              'zred': 1.,
              # Data manipulation parameters
              'logify_spectrum':False,
              'normalize_spectrum':False,
              # SPS parameters
              'zcontinuous': 1,
              }

# --------------
# OBS
# --------------
def load_obs(snr=10.0, add_noise=True, **kwargs):
    """Make a mock dataset.  Feel free to add more complicated kwargs, and put
    other things in the run_params dictionary to control how the mock is
    generated.

    :param snr:
        The S/N of the phock photometry.  This can also be a vector of same
        lngth as the number of filters.

    :param add_noise: (optional, boolean, default: True)
        If True, add a realization of the noise to the mock spectrum
    """

    # first, load the filters. let's use the GOODSN filter set.
    # XX: rewrite as necessary to find the filters folder
    filter_folder = os.getenv('APPS')+'/spatialsed/filters/'
    fname_all = os.listdir(filter_folder)
    fname_goodsn = [f.split('.')[0] for f in fname_all if 'goodsn' in f]

    # now separate into components. we will generate separate observations for
    # HST bands.
    fname_hst = ['f435w','f606w','f775w','f850lp','f125w','f140w','f160w']
    n_hst = len(fname_hst)
    n_blended = len(fname_goodsn) - n_hst
    component = np.array(np.zeros(n_hst).tolist() + np.ones(n_hst).tolist() + np.repeat(-1,n_blended).tolist(),dtype=int)

    # generate filter list. repeat HST filters
    fname_ground = [s for s in fname_goodsn if s.split('_')[0] not in fname_hst]
    fnames = 2*[f+'_goodsn' for f in fname_hst] + fname_ground
    filters = load_filters(fnames, directory=filter_folder)

    # now generate data
    # we will need the models to make a mock
    sps = load_sps(**kwargs)
    mod = load_model(**kwargs)

    # we will also need an obs dictionary
    obs = {}
    obs['filters'] = filters
    obs['component'] = component
    obs['wavelength'] = None

    # Now we get the mock params from the kwargs dict
    params = {}
    for p in mod.params.keys():
        if p in kwargs:
            params[p] = np.atleast_1d(kwargs[p])

    # Generate the photometry, add noise
    mod.params.update(params)
    spec, phot, _ = mod.mean_model(mod.theta, obs, sps=sps)
    pnoise_sigma = phot / snr
    if add_noise:
        pnoise = np.random.normal(0, 1, len(phot)) * pnoise_sigma
        maggies = phot + pnoise
    else:
        maggies = phot.copy()

    # Now store output in standard format
    obs['maggies'] = maggies
    obs['maggies_unc'] = pnoise_sigma
    obs['mock_snr'] = snr
    obs['phot_mask'] = np.ones(len(phot), dtype=bool)

    # we also keep the unessential mock information
    obs['true_spectrum'] = spec.copy()
    obs['true_maggies'] = phot.copy()
    obs['mock_params'] = deepcopy(mod.params)

    return obs

    """ plotting code
    wave_eff = np.log10([filt.wave_effective for filt in obs['filters']])
    flux = np.log10(obs['maggies'])
    fluxerr = np.zeros_like(flux)+0.05
    bulge = obs['component'] == 0
    disk = obs['component'] == 1
    total = obs['component'] == -1

    plt.errorbar(wave_eff,flux,yerr=fluxerr,color='black',linestyle=' ',label='total',fmt='o')
    plt.errorbar(wave_eff[bulge], flux[bulge], yerr=fluxerr[bulge], color='red', linestyle=' ', fmt='o', label='bulge')
    plt.errorbar(wave_eff[disk], flux[disk], yerr=fluxerr[disk], color='blue', linestyle=' ', fmt='o', label='disk')
    plt.legend()
    plt.show()

    """

# --------------
# New Source and Model Objects
# --------------

from prospect.sources import CSPSpecBasis
from prospect.models.sedmodel import SedModel
from prospect.sources.constants import lightspeed, jansky_cgs, to_cgs_at_10pc
to_cgs = to_cgs_at_10pc

class SpatialSource(CSPSpecBasis):

    def get_galaxy_spectrum(self, **params):
        """Update parameters, then loop over each component getting a spectrum
        for each.  Return all the component spectra, plus the sum

        :param params:
            A parameter dictionary that gets passed to the ``self.update``
            method and will generally include physical parameters that control
            the stellar population and output spectrum or SED.

        :returns wave:
            Wavelength in angstroms.

        :returns spectrum:
            Spectrum in units of Lsun/Hz/solar masses formed.  ndarray of
            shape(ncomponent+1, nwave)

        :returns mass_fraction:
            Fraction of the formed stellar mass that still exists.
        """
        self.update(**params)
        spectra = []
        mass = np.atleast_1d(self.params['mass']).copy()
        mfrac = np.zeros_like(mass)
        # Loop over mass components
        for i, m in enumerate(mass):
            self.update_component(i)
            wave, spec = self.ssp.get_spectrum(tage=self.ssp.params['tage'],
                                               peraa=False)
            spectra.append(spec)
            mfrac[i] = (self.ssp.stellar_mass)

        # Convert normalization units from per stellar mass to per mass formed
        if np.all(self.params.get('mass_units', 'mformed') == 'mstar'):
            mass /= mfrac
        spectrum = np.dot(mass, np.array(spectra)) / mass.sum()
        mfrac_sum = np.dot(mass, mfrac) / mass.sum()

        return wave, np.squeeze(spectra + [spectrum]), np.squeeze(mfrac.tolist() + [mfrac_sum])

    def get_spectrum(self, outwave=None, filters=None, component=-1, **params):
        """
        """
        # Spectrum in Lsun/Hz per solar mass formed, restframe
        wave, spectrum, mfrac = self.get_galaxy_spectrum(**params)

        # Redshifting + Wavelength solution
        # We do it ourselves.
        a = 1 + self.params.get('zred', 0)
        af = a
        b = 0.0

        if 'wavecal_coeffs' in self.params:
            x = wave - wave.min()
            x = 2.0 * (x / x.max()) - 1.0
            c = np.insert(self.params['wavecal_coeffs'], 0, 0)
            # assume coeeficients give shifts in km/s
            b = chebval(x, c) / (lightspeed*1e-13)

        wa, sa = wave * (a + b), spectrum * af  # Observed Frame
        if outwave is None:
            outwave = wa

        # Observed frame photometry, as absolute maggies
        if filters is not None:
            # Magic to only do filter projections for unique filters, and get a
            # mapping back into this list of unique filters
            # note that this may scramble order of unique_filters
            fnames = [f.name for f in filters]
            unique_names, uinds, filter_ind = np.unique(fnames, return_index=True, return_inverse=True)
            unique_filters = np.array(filters)[uinds]
            mags = getSED(wa, lightspeed/wa**2 * sa * to_cgs, unique_filters)
            phot = np.atleast_1d(10**(-0.4 * mags))
        else:
            phot = 0.0

        # Distance dimming and unit conversion
        zred = self.params.get('zred', 0.0)
        if (zred == 0) or ('lumdist' in self.params):
            # Use 10pc for the luminosity distance (or a number
            # provided in the dist key in units of Mpc)
            dfactor = (self.params.get('lumdist', 1e-5) * 1e5)**2
        else:
            lumdist = cosmo.luminosity_distance(zred).value
            dfactor = (lumdist * 1e5)**2

        # Spectrum will be in maggies
        sa *= to_cgs / dfactor / (3631*jansky_cgs)

        # Convert from absolute maggies to apparent maggies
        phot /= dfactor

        # Mass normalization
        mass = np.atleast_1d(self.params['mass'])
        mass = np.squeeze(mass.tolist() + [mass.sum()])

        sa = (sa * mass[:, None])
        phot = (phot * mass[:, None])[component, filter_ind]

        return sa, phot, mfrac


class SpatialSedModel(SedModel):

    def sed(self, theta, obs, sps=None, **kwargs):
        """Given a theta vector, generate a spectrum, photometry, and any
        extras (e.g. stellar mass), ***not** including any instrument
        calibration effects.

        :param theta:
            ndarray of parameter values.

        :param sps:
            A StellarPopBasis object to be used
            in the model generation.

        :returns spec:
            The model spectrum for these parameters, at the wavelengths
            specified by obs['wavelength'], in linear units.

        :returns phot:
            The model photometry for these parameters, for the filters
            specified in obs['filters'].

        :returns extras:
            Any extra aspects of the model that are returned.
        """

        self.set_parameters(theta)
        spec, phot, extras = sps.get_spectrum(outwave=obs['wavelength'],
                                              filters=obs['filters'],
                                              component=obs.get('component', -1),
                                              lnwavegrid=obs.get('lnwavegrid', None),
                                              **self.params)

        spec *= obs.get('normalization_guess', 1.0)
        # Remove negative fluxes.
        try:
            tiny = 1.0/len(spec) * spec[spec > 0].min()
            spec[spec < tiny] = tiny
        except:
            pass
        spec = (spec + self.sky())
        self._spec = spec.copy()
        return spec, phot, extras

    def spec_calibration(self, **extras):
        return 1.0

    
# --------------
# SPS Object
# --------------


def load_sps(zcontinuous=1, compute_vega_mags=False, **extras):
    sps = SpatialSource(zcontinuous=zcontinuous,
                        compute_vega_mags=compute_vega_mags)
    return sps

# -----------------
# Noise Model
# ------------------

def load_gp(**extras):
    return None, None

# --------------
# MODEL_PARAMS
# --------------

# You'll note below that we have 5 free parameters:
# mass, logzsol, tage, tau, dust2
# They are all scalars.
# mass and tau have logUniform priors (i.e. TopHat priors in log(mass) and
# log(tau)), the rest have TopHat priors.
# You should adjust the prior ranges (particularly in mass) to suit your objects.
#
# The other parameters are all fixed, but we may want to explicitly set their
# values, which can be done here, to override any defaults in python-FSPS


model_params = []

# --- Distance ---
model_params.append({'name': 'zred', 'N': 1,
                        'isfree': False,
                        'init': 0.1,
                        'units': '',
                        'prior': priors.TopHat(mini=0.0, maxi=4.0)})

# --- SFH --------
# FSPS parameter
model_params.append({'name': 'sfh', 'N': 1,
                        'isfree': False,
                        'init': 4,  # This is delay-tau
                        'units': 'type',
                        'prior': None})

model_params.append({'name': 'mass', 'N': 2,
                        'isfree': True,
                        'init': 1e10,
                        'init_disp': 1e9,
                        'units': r'M_\odot',
                        'prior': priors.LogUniform(mini=1e8, maxi=1e12)})

model_params.append({'name': 'logzsol', 'N': 2,
                        'isfree': True,
                        'init': -0.3,
                        'init_disp': 0.3,
                        'units': r'$\log (Z/Z_\odot)$',
                        'prior': priors.TopHat(mini=-1.5, maxi=0.19)})

# If zcontinuous > 1, use 3-pt smoothing
model_params.append({'name': 'pmetals', 'N': 1,
                        'isfree': False,
                        'init': -99,
                        'prior': None})
                        
# FSPS parameter
model_params.append({'name': 'tau', 'N': 2,
                        'isfree': True,
                        'init': 1.0,
                        'init_disp': 0.5,
                        'units': 'Gyr',
                        'prior':priors.LogUniform(mini=0.101, maxi=100)})

# FSPS parameter
model_params.append({'name': 'tage', 'N': 2,
                        'isfree': True,
                        'init': 5.0,
                        'init_disp': 3.0,
                        'units': 'Gyr',
                        'prior': priors.TopHat(mini=0.01, maxi=14.0)})


# --- Dust ---------
# FSPS parameter
model_params.append({'name': 'dust2', 'N': 2,
                        'isfree': True,
                        'init': 0.35,
                        'reinit': True,
                        'init_disp': 0.3,
                        'units': 'Diffuse dust optical depth towards all stars at 5500AA',
                        'prior': priors.TopHat(mini=0.0, maxi=2.0)})

# FSPS parameter
model_params.append({'name': 'dust_index', 'N': 1,
                        'isfree': False,
                        'init': -0.7,
                        'units': 'power law slope of the attenuation curve for diffuse dust',
                        'prior': None,})

# FSPS parameter
model_params.append({'name': 'dust1_index', 'N': 1,
                        'isfree': False,
                        'init': -1.0,
                        'units': 'power law slope of the attenuation curve for young-star dust',
                        'prior': None,})

# FSPS parameter
model_params.append({'name': 'dust_type', 'N': 1,
                        'isfree': False,
                        'init': 0,  # power-laws
                        'units': 'index',
                        'prior': None})

# FSPS parameter
model_params.append({'name': 'add_dust_emission', 'N': 1,
                        'isfree': False,
                        'init': True,
                        'units': 'index',
                        'prior': None})

# An example of the parameters controlling the dust emission SED.  There are others!
model_params.append({'name': 'duste_umin', 'N': 1,
                        'isfree': False,
                        'init': 1.0,
                        'units': 'MMP83 local MW intensity',
                        'prior': None})

model_params.append({'name': 'duste_qpah', 'N': 1,
                        'isfree': False,
                        'init': 2.0,
                        'units': 'MMP83 local MW intensity',
                        'prior': None})


# --- Nebular Emission ------

# For speed we turn off nebular emission in the demo
model_params.append({'name': 'add_neb_emission', 'N': 1,
                        'isfree': False,
                        'init': False,
                        'prior': None})

# Here is a really simple function that takes a **dict argument, picks out the
# `logzsol` key, and returns the value.  This way, we can have gas_logz find
# the value of logzsol and use it, if we uncomment the 'depends_on' line in the
# `gas_logz` parameter definition.
#
# One can use this kind of thing to transform parameters as well (like making
# them linear instead of log, or divide everything by 10, or whatever.) You can
# have one parameter depend on several others (or vice versa).  Just remember
# that a parameter with `depends_on` must always be fixed.

def stellar_logzsol(logzsol=0.0, **extras):
    return logzsol

# FSPS parameter
model_params.append({'name': 'gas_logz', 'N': 1,
                        'isfree': False,
                        'init': 0.0,
                        'units': r'log Z/Z_\odot',
#                        'depends_on': stellar_logzsol,
                        'prior': priors.TopHat(mini=-2.0, maxi=0.5)})

# FSPS parameter
model_params.append({'name': 'gas_logu', 'N': 1,
                        'isfree': False,
                        'init': -2.0,
                        'units': '',
                        'prior': priors.TopHat(mini=-4, maxi=-1)})

# --- Calibration ---------
# Only important if using a NoiseModel
model_params.append({'name': 'phot_jitter', 'N': 1,
                        'isfree': False,
                        'init': 0.0,
                        'units': 'mags',
                        'prior': priors.TopHat(mini=0.0, maxi=0.2)})


def load_model(zred=0.0, **extras):
    # In principle (and we've done it) you could have the model depend on
    # command line arguments (or anything in run_params) by making changes to
    # `model_params` here before instantiation the SedModel object.  Up to you.

    # Here we are going to set the intial value (and the only value, since it
    # is not a free parameter) of the redshift parameter to whatever was used
    # to generate the mock, listed in the run_params dictionary.
    pn = [p['name'] for p in model_params]
    zind = pn.index('zred')
    model_params[zind]['init'] = zred
    
    return SpatialSedModel(model_params)


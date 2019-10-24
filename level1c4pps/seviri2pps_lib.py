#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2019 level1c4pps developers
#
# This file is part of level1c4pps
#
# atrain_match is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# atrain_match is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with atrain_match.  If not, see <http://www.gnu.org/licenses/>.
# Author(s):

#   Martin Raspaud <martin.raspaud@smhi.se>
#   Nina Hakansson <nina.hakansson@smhi.se>
#   Adam.Dybbroe <adam.dybbroe@smhi.se>

# This program was developed by CMSAF to be used for the processing of
# CLAAS3.

"""Tools to convert SEVIRI hrit to PPS level-1c format."""


import os
import numpy as np
import xarray as xr
import dask.array as da
from glob import glob
import time
from datetime import datetime
from satpy.scene import Scene
import satpy.utils
from trollsift.parser import globify, Parser
from pyorbital.astronomy import get_alt_az, sun_zenith_angle
from pyorbital.orbital import get_observer_look

from level1c4pps.calibration_coefs import get_calibration_for_time, CALIB_MODE
from level1c4pps import make_azidiff_angle


class UnexpectedSatpyVersion(Exception):
    """Exception if unexpected satpy version."""

    pass


BANDNAMES = ['VIS006', 'VIS008', 'IR_016', 'IR_039',
             'IR_087', 'IR_108', 'IR_120',
             'IR_134', 'IR_097', 'WV_062', 'WV_073']
PPS_TAGNAMES = {'VIS006': 'ch_r06',
                'VIS008': 'ch_r09',
                'IR_016': 'ch_r16',
                'IR_039': 'ch_tb37',
                'IR_087': 'ch_tb85',
                'IR_108': 'ch_tb11',
                'IR_120': 'ch_tb12',
                'IR_134': 'ch_tb133',
                'IR_097': 'ch_tb97',
                'WV_062': 'ch_tb67',
                'WV_073': 'ch_tb73'}

# H-000-MSG3__-MSG3________-IR_120___-000003___-201410051115-__:
hrit_file_pattern = '{rate:1s}-000-{hrit_format:_<6s}-{platform_shortname:_<12s}-{channel:_<8s}_-{segment:_<9s}-{start_time:%Y%m%d%H%M}-__'
p__ = Parser(hrit_file_pattern)


def rotate_band(scene, band):
    """Rotate band by 180 degrees."""
    scene[band] = scene[band].reindex(x=scene[band].x[::-1],
                                      y=scene[band].y[::-1])
    llx, lly, urx, ury = scene[band].attrs['area'].area_extent
    scene[band].attrs['area'] = scene[band].attrs['area'].copy(
        area_extent=[urx, ury, llx, lly])


def get_lonlats(dataset):
    """Get lat/lon coordinates."""
    lons, lats = dataset.attrs['area'].get_lonlats()
    lons[np.fabs(lons) > 360] = -999.0
    lats[np.fabs(lons) > 90] = -999.0
    return lons, lats


def get_solar_angles(dataset, lons, lats):
    """Compute solar angles.

    Returns:
        Solar azimuth angle, Solar zenith angle in degrees
    """
    _, suna = get_alt_az(dataset.attrs['start_time'], lons, lats)
    suna = np.rad2deg(suna)
    sunz = sun_zenith_angle(dataset.attrs['start_time'], lons, lats)
    return suna, sunz


def get_satellite_angles(dataset, lons, lats):
    """Compute satellite angles.

    Returns:
        Satellite azimuth angle, Satellite zenith angle in degrees
    """
    # if:
    #   1) get_observer_look() gives wrong answer ...
    #   ... for satellite altitude in m. AND
    #   2) get_observer_look() gives correct answer ...
    #   ....  for satellite altitude in km. AND
    #   3) Satellite altitude is m.:
    #    => Satellite alltitude need to be converted to km.
    # else:
    #    => There have been updates to SatPy and this script
    #       need to be modified.
    sat_lon, sat_lat, sat_alt = satpy.utils.get_satpos(dataset)
    # Double check that pyorbital/satpy behave as expected (satpy returning
    # altitude in meters and pyorbital expecting km)
    if not (get_observer_look(0, 0, 36000*1000,
                              datetime.utcnow(), np.array([16]),
                              np.array([58]), np.array([0]))[1] > 30 and
            get_observer_look(0, 0, 36000,
                              datetime.utcnow(), np.array([16]),
                              np.array([58]), np.array([0]))[1] < 23 and
            sat_alt > 38000):
        raise UnexpectedSatpyVersion(
            'Unexpected handling of satellite altitude in pyorbital/'
            'satpy. Conversion to km is probably unneeded and wrong.')

    # Convert altitude from meters to kilometers, as expected by the
    # current version of pyorbital
    sat_alt *= 0.001

    # Compute angles
    sata, satel = get_observer_look(
        sat_lon,
        sat_lat,
        sat_alt,
        dataset.attrs['start_time'],
        lons, lats, 0)
    satz = 90 - satel

    return sata, satz


def set_attrs(scene):
    """Set global and band attributes."""
    # Global
    scene.attrs['platform'] = scene['IR_108'].attrs['platform_name']
    scene.attrs['instrument'] = 'SEVIRI'
    scene.attrs['source'] = "seviri2pps.py"
    scene.attrs['orbit_number'] = "99999"
    nowutc = datetime.utcnow()
    scene.attrs['date_created'] = nowutc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # For each band
    for image_num, band in enumerate(BANDNAMES):
        idtag = PPS_TAGNAMES[band]
        scene[band].attrs['id_tag'] = idtag
        scene[band].attrs['description'] = 'SEVIRI ' + str(band)
        scene[band].attrs['sun_earth_distance_correction_applied'] = 'False'
        scene[band].attrs['sun_earth_distance_correction_factor'] = 1.0
        scene[band].attrs['sun_zenith_angle_correction_applied'] = 'False'
        scene[band].attrs['name'] = "image{:d}".format(image_num)


def set_coords(scene):
    """Set band coordinates."""
    for band in BANDNAMES:
        # Remove area, set lat/lon as coordinates
        scene[band].attrs.pop('area', None)
        scene[band].attrs['coordinates'] = 'lon lat'

        # Add time coordinate to make cfwriter aware that we want 3D data
        scene[band].coords['time'] = scene[band].attrs['start_time']
        scene[band] = scene[band].drop(['acq_time'])


def add_ancillary_dataset(scene, lons, lats, sunz, satz, azidiff, chunks=(53, 3712)):
    """Add ancillary datasets to the scene.

    Args:
        lons: Longitude coordinates
        lats: Latitude coordinates
        sunz: Solar zenith angle
        satz: Satellite zenith angle
        azidiff: Absoulte azimuth difference angle
        chunks: Chunksize
    """
    start_time = scene['IR_108'].attrs['start_time']
    end_time = scene['IR_108'].attrs['end_time']
    angle_coords = scene['IR_108'].coords
    angle_coords['time'] = start_time

    # Latitude
    scene['lat'] = xr.DataArray(
        da.from_array(lats, chunks=chunks),
        dims=['y', 'x'],
        coords={'y': scene['IR_108']['y'], 'x': scene['IR_108']['x']})
    scene['lat'].attrs['long_name'] = 'latitude coordinate'
    scene['lat'].attrs['standard_name'] = 'latitude'
    scene['lat'].attrs['units'] = 'degrees_north'
    scene['lat'].attrs['start_time'] = start_time
    scene['lat'].attrs['end_time'] = end_time

    # Longitude
    scene['lon'] = xr.DataArray(
        da.from_array(lons, chunks=chunks),
        dims=['y', 'x'],
        coords={'y': scene['IR_108']['y'], 'x': scene['IR_108']['x']})
    scene['lon'].attrs['long_name'] = 'longitude coordinate'
    scene['lon'].attrs['standard_name'] = 'longitude'
    scene['lon'].attrs['units'] = 'degrees_east'
    scene['lon'].attrs['start_time'] = start_time
    scene['lon'].attrs['end_time'] = end_time

    # Sunzenith
    scene['sunzenith'] = xr.DataArray(
        da.from_array(sunz[:, :], chunks=chunks),
        dims=['y', 'x'], coords=angle_coords)
    scene['sunzenith'].attrs['id_tag'] = 'sunzenith'
    scene['sunzenith'].attrs['long_name'] = 'sun zenith angle'
    scene['sunzenith'].attrs['standard_name'] = 'solar_zenith_angle'
    scene['sunzenith'].attrs['valid_range'] = [0, 18000]
    scene['sunzenith'].attrs['name'] = "image11"

    # Satzenith
    scene['satzenith'] = xr.DataArray(
        da.from_array(satz[:, :], chunks=chunks),
        dims=['y', 'x'], coords=angle_coords)
    scene['satzenith'].attrs['id_tag'] = 'satzenith'
    scene['satzenith'].attrs['long_name'] = 'satellite zenith angle'
    scene['satzenith'].attrs['standard_name'] = 'platform_zenith_angle'
    scene['satzenith'].attrs['valid_range'] = [0, 9000]
    scene['satzenith'].attrs['name'] = "image12"

    # Azidiff
    scene['azimuthdiff'] = xr.DataArray(
        da.from_array(azidiff[:, :], chunks=chunks),
        dims=['y', 'x'], coords=angle_coords)
    scene['azimuthdiff'].attrs['id_tag'] = 'azimuthdiff'
    # scene['azimuthdiff'].attrs['standard_name'] = (
    #    'angle_of_rotation_from_solar_azimuth_to_platform_azimuth')  # FIXME
    scene['azimuthdiff'].attrs['long_name'] = 'absoulte azimuth difference angle'
    scene['azimuthdiff'].attrs['valid_range'] = [0, 18000]
    scene['azimuthdiff'].attrs['name'] = "image13"

    # Some common attributes
    for angle in ['azimuthdiff', 'satzenith', 'sunzenith']:
        scene[angle].attrs['units'] = 'degree'
        for attr in ["start_time", "end_time", "orbital_parameters",
                     "georef_offset_corrected"]:
            scene[angle].attrs[attr] = scene['IR_108'].attrs[attr]


def compose_filename(scene, out_path):
    """Compose output filename."""
    start_time = scene['IR_108'].attrs['start_time']  # FIXME: scene.attrs['start_time'] ?
    end_time = scene['IR_108'].attrs['end_time']
    platform_name = scene.attrs['platform']
    filename = os.path.join(
        out_path,
        "S_NWC_seviri_{:s}_{:s}_{:s}Z_{:s}Z.nc".format(
            platform_name.lower().replace('-', ''),
            "99999",
            start_time.strftime('%Y%m%dT%H%M%S%f')[:-5],
            end_time.strftime('%Y%m%dT%H%M%S%f')[:-5]))
    return filename


def get_encoding(scene):
    """Get netcdf encoding for all datasets."""
    encoding = {}

    # Bands
    for band in BANDNAMES:
        idtag = PPS_TAGNAMES[band]
        name = scene[band].attrs['name']
        if 'tb' in idtag:
            encoding[name] = {'dtype': 'int16',
                              'scale_factor': 0.01,
                              '_FillValue': -32767,
                              'zlib': True,
                              'complevel': 4,
                              'add_offset': 273.15}
        else:
            encoding[name] = {'dtype': 'int16',
                              'scale_factor': 0.01,
                              'zlib': True,
                              'complevel': 4,
                              '_FillValue': -32767,
                              'add_offset': 0.0}

    # Angles and lat/lon
    for name in ['image11', 'image12', 'image13']:
        encoding[name] = {
            'dtype': 'int16',
            'scale_factor': 0.01,
            'zlib': True,
            'complevel': 4,
            '_FillValue': -32767,
            'add_offset': 0.0}

    for name in ['lon', 'lat']:
        encoding[name] = {'dtype': 'float32',
                          'zlib': True,
                          'complevel': 4,
                          '_FillValue': -999.0}

    return encoding


def get_header_attrs(scene):
    """Get global netcdf attributes."""
    header_attrs = scene.attrs.copy()
    header_attrs['start_time'] = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        scene.attrs['start_time'].timetuple())
    header_attrs['end_time'] = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        scene.attrs['end_time'].timetuple())
    header_attrs['sensor'] = 'seviri'
    return header_attrs


def process_one_scan(tslot_files, out_path):
    """Make level 1c files in PPS-format."""
    tic = time.time()
    platform_shortname = p__.parse(
        os.path.basename(tslot_files[0]))['platform_shortname']
    start_time = p__.parse(
        os.path.basename(tslot_files[0]))['start_time']

    # Load and calibrate data using inter-calibration coefficients from
    # Meirink et al
    coefs = get_calibration_for_time(platform=platform_shortname,
                                     time=start_time)
    scn_ = Scene(reader='seviri_l1b_hrit',
                 filenames=tslot_files,
                 reader_kwargs={'calib_mode': CALIB_MODE,
                                'ext_calib_coefs': coefs})
    if not scn_.attrs['sensor'] == {'seviri'}:
        raise ValueError('Not SEVIRI data')
    scn_.load(BANDNAMES)

    # By default pixel (0,0) is S-E. Rotate bands so that (0,0) is N-W.
    for band in BANDNAMES:
        rotate_band(scn_, band)

    # Find lat/lon data
    lons, lats = get_lonlats(scn_['IR_108'])

    # Compute angles
    suna, sunz = get_solar_angles(scn_['IR_108'], lons=lons, lats=lats)
    sata, satz = get_satellite_angles(scn_['IR_108'], lons=lons, lats=lats)
    azidiff = make_azidiff_angle(sata, suna)

    # Update coordinates
    set_coords(scn_)

    # Add ancillary datasets to the scen
    add_ancillary_dataset(scn_, lons=lons, lats=lats, sunz=sunz, satz=satz,
                          azidiff=azidiff)

    # Set attributes. This changes SEVIRI band names to PPS band names.
    set_attrs(scn_)

    # Write datasets to netcdf
    filename = compose_filename(scene=scn_, out_path=out_path)
    scn_.save_datasets(writer='cf',
                       filename=filename,
                       header_attrs=get_header_attrs(scn_),
                       engine='netcdf4',
                       encoding=get_encoding(scn_),
                       include_lonlats=False,
                       pretty=True,
                       flatten_attrs=True,
                       exclude_attrs=['raw_metadata'])
    print("Saved file {:s} after {:3.1f} seconds".format(
        os.path.basename(filename),
        time.time()-tic))  # About 40 seconds
    return filename


def process_all_scans_in_dname(dname, out_path, ok_dates=None):
    """Make level 1c files for all files in directory dname."""
    fl_ = glob(os.path.join(dname, globify(hrit_file_pattern)))
    dates = [p__.parse(os.path.basename(p))['start_time'] for p in fl_]
    unique_dates = np.unique(dates).tolist()
    for uqdate in unique_dates:
        date_formated = uqdate.strftime("%Y%m%d%H%M")
        if ok_dates is not None and date_formated not in ok_dates.keys():
            print("Skipping date {date}".format(date=date_formated))
            continue
        # Every hour only:
        # if uqdate.minute != 0:
        #    continue
        tslot_files = [f for f in fl_ if p__.parse(
            os.path.basename(f))['start_time'] == uqdate]
        try:
            process_one_scan(tslot_files, out_path)
        except:
            pass

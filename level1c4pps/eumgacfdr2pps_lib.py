#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2019 level1c4pps developers
#
# This file is part of level1c4pps
#
# level1c4pps is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# level1c4pps is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with level1c4pps.  If not, see <http://www.gnu.org/licenses/>.
# Author(s):

#   Martin Raspaud <martin.raspaud@smhi.se>
#   Nina Hakansson <nina.hakansson@smhi.se>
#   Adam.Dybbroe <adam.dybbroe@smhi.se>

"""Utilities to convert AVHRR GAC formattet data to PPS level-1c format."""


import os
import time
import xarray as xr
import dask.array as da
import numpy as np
from datetime import datetime
from satpy.scene import Scene
import pygac  # testing that pygac is available # noqa: F401
from level1c4pps import (get_encoding, compose_filename,
                         set_header_and_band_attrs_defaults,
                         PPS_ANGLE_TAGS,
                         rename_latitude_longitude, update_angle_attributes,
                         get_header_attrs, convert_angles)
import logging
from satpy.utils import debug_on
debug_on()

# AVHRR-GAC_FDR_1C_N06_19810330T005421Z_19810330T024632Z_R_O_20200101T000000Z_0100.nc

logger = logging.getLogger('eumgacfdr2pps')

BANDNAMES = ['reflectance_channel_1',
             'reflectance_channel_2',
             'reflectance_channel_3',
             'brightness_temperature_channel_3',
             'brightness_temperature_channel_4',
             'brightness_temperature_channel_5']

REFL_BANDS = ['reflectance_channel_1', 'reflectance_channel_2', 'reflectance_channel_3']

PPS_TAGNAMES = {"reflectance_channel_1": "ch_r06",
                "reflectance_channel_2": "ch_r09",
                "reflectance_channel_3": "ch_r16",
                "brightness_temperature_channel_3": "ch_tb37",
                "brightness_temperature_channel_4": "ch_tb11",
                "brightness_temperature_channel_5": "ch_tb12"}

REMOVE_ATTRIBUTES = ['_satpy_id', 'creator_email',
                     'comment', 'creator_url',
                     'date_created', 'disposition_mode',
                     'institution', 
                     'keywords', 'keywords_vocabulary',
                     'naming_authority',
                     'processing_mode']

MOVE_TO_HEADER = ['gac_filename',
                  'geospatial_lat_max',
                  'geospatial_lat_min',
                  'geospatial_lat_units',
                  'geospatial_lon_max',
                  'geospatial_lon_min',
                  'geospatial_lon_units',
                  'ground_station',
                  'history',
                  'orbital_parameters_tle',
                  'orbit_number_end',
                  'orbit_number_start',
                  'references',
                  'source',
                  'standard_name_vocabulary',
                  'summary',
                  'time_coverage_end',
                  'time_coverage_start',
                  'title',
                  'version_calib_coeffs',
                  'version_pygac',
                  'version_pygac_fdr']

BAND_ATTRIBUTES = ['valid_min', 'valid_max', 'coordinates', 'resolution',
                   'calibration', 'polarization', 'level', 'modifiers']

RENAME_AND_MOVE_TO_HEADER = {'id': 'euemtsat_gac_id',
                             'licence': 'eumetsat_licence',
                             'product_version': 'eumetsat_product_version',
                             'version_satpy': 'eumetsat_pygac_fdr_satpy_version'}

COPY_TO_HEADER = ['start_time', 'end_time']


def get_encoding_gac(scene):
    """Get netcdf encoding for all datasets."""
    return get_encoding(scene,
                        BANDNAMES,
                        PPS_TAGNAMES,
                        chunks=None)


def update_ancilliary_datasets(scene):
    """Rename, delete and add some datasets and attributes."""
    irch = scene['brightness_temperature_channel_4']

    # Create new data set scanline timestamps
    scene['scanline_timestamps'] = scene['acq_time']
    scene['scanline_timestamps'].attrs['name'] = 'scanline_timestamps'
    del scene['acq_time'].coords['acq_time']
    del scene['acq_time']

    # Update qual_flags attrs
    scene['qual_flags'].attrs['id_tag'] = 'qual_flags'
    scene['qual_flags'].attrs['long_name'] = 'pygac quality flags'
    scene['qual_flags'].coords['time'] = irch.attrs['start_time']
    del scene['qual_flags'].coords['acq_time']
    attrs_to_delete = BAND_ATTRIBUTES
    remove_header_attributes_from_bands(scene, 'scanline_timestamps', remove_extra=attrs_to_delete)
    remove_header_attributes_from_bands(scene, 'qual_flags', remove_extra=attrs_to_delete)                                   
    remove_header_attributes_from_bands(scene, 'overlap_free_end', remove_extra=attrs_to_delete)    
    remove_header_attributes_from_bands(scene, 'overlap_free_end', remove_extra=attrs_to_delete)    
    remove_header_attributes_from_bands(scene, 'midnight_line', remove_extra=attrs_to_delete)

def remove_header_attributes_from_bands(scene, band, remove_extra=[]):
    for attr in remove_extra + REMOVE_ATTRIBUTES + MOVE_TO_HEADER + list(RENAME_AND_MOVE_TO_HEADER.keys()):
        try:
            del scene[band].attrs[attr]  
        except KeyError:
            pass

def remove_angle_attributes(scene):
    for angle in PPS_ANGLE_TAGS:
        remove_header_attributes_from_bands(scene, angle)
    
def set_header_and_band_attrs(scene):
    """Set and delete some attributes."""
    irch = scene['brightness_temperature_channel_4']
    for attr in ['platform', 'instrument', 'sensor']:
        if attr in irch.attrs:
            if '>' in irch.attrs[attr]:
                scene.attrs[attr] = irch.attrs[attr].split('>')[-1].strip()
    nimg = set_header_and_band_attrs_defaults(scene, BANDNAMES, PPS_TAGNAMES, REFL_BANDS, irch)
    scene.attrs['source'] = "eumgacfdr2pps.py"
    # Are these really needed?
    scene.attrs['orbit_number'] = int(99999)
    for attr in MOVE_TO_HEADER + COPY_TO_HEADER:
        try:
            scene.attrs[attr] = irch.attrs[attr]
        except KeyError:
            pass
        
    for attr in RENAME_AND_MOVE_TO_HEADER:
        scene.attrs[RENAME_AND_MOVE_TO_HEADER[attr]] = irch.attrs[attr]
    for band in BANDNAMES:
        if band not in scene:
            continue
        if band in REFL_BANDS:
            # For GAC data sun_earth_distance_correction is applied always!
            # The sun_earth_distance_correction_factor is not provided by pygac <= 1.2.1 / satpy <= 0.18.1
            scene[band].attrs['sun_earth_distance_correction_applied'] = 'True'
            scene[band].attrs['sun_earth_distance_correction_factor'] = irch.attrs['sun_earth_distance_correction_factor']
        scene[band].attrs['platform'] = scene.attrs['platform']
        scene[band].attrs['instrument'] = scene.attrs['instrument']
        remove_header_attributes_from_bands(scene, band)
    return nimg


def process_one_file(eumgacfdr_file, out_path='.', reader_kwargs=None):
    """Make level 1c files in PPS-format."""
    tic = time.time()
    scn_ = Scene(reader='eum_gac_fdr_nc',
                 filenames=[eumgacfdr_file])

    # Loading all at once sometimes fails with newer satpy, so start with BANDNAMES ...

    scn_.load(BANDNAMES)
    scn_.load(['latitude',
               'longitude',
               'qual_flags',
               'acq_time',
               'overlap_free_end',
               'overlap_free_end',
               'midnight_line',
               'sensor_zenith_angle', 'solar_zenith_angle',
               'solar_azimuth_angle', 'sensor_azimuth_angle',
               'sun_sensor_azimuth_difference_angle'])


    # one ir channel
    irch = scn_['brightness_temperature_channel_4']
    scn_['latitude'] = scn_['brightness_temperature_channel_4'].coords['latitude']
    scn_['longitude'] = scn_['brightness_temperature_channel_4'].coords['longitude']
    scn_['acq_time'] = scn_['brightness_temperature_channel_4'].coords['acq_time']
     
    # Set header and band attributes
    set_header_and_band_attrs(scn_)

    # Rename longitude, latitude to lon, lat.
    rename_latitude_longitude(scn_)

    # Convert angles to PPS
    convert_angles(scn_)
    update_angle_attributes(scn_, irch)
    remove_angle_attributes(scn_)

    # Handle gac specific datasets qual_flags and scanline_timestamps
    update_ancilliary_datasets(scn_)
    filename = compose_filename(scn_, out_path, instrument='avhrr', band=irch)
    scn_.save_datasets(writer='cf',
                       filename=filename,
                       header_attrs=get_header_attrs(scn_, band=irch, sensor='avhrr'),
                       engine='netcdf4',
                       flatten_attrs=True,
                       include_lonlats=False,  # Included anyway as they are datasets in scn_
                       pretty=True,
                       encoding=get_encoding_gac(scn_))

    print("Saved file {:s} after {:3.1f} seconds".format(
        os.path.basename(filename),
        time.time()-tic))
    return filename

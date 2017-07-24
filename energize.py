#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Jun 13 11:05:54 2017

@author: matt
"""

import pandas as pd
import numpy as np
from icalendar import Calendar
import pytz
import datetime
import matplotlib.pyplot as plt
import matplotlib.mlab as mlab
from scipy import optimize
from scipy import stats
import math

"""
range_token_df: DataFrame, RangeToken --> DataFrame
Returns a dataframe filtered by the range token provided.

A RangeToken is either a datetime index (parial or formal)
or a tuple of start/end datetime indexes
"""
def range_token_df(data, token):
    if (type(token)==str):
        try:
            return data[token]
        except KeyError: #returns None
            print('[!] energize.py : range_token_df : ' + token+' not in range')
    else: # token is a start/end tuple
        return data[slice(*token)]

"""
data_in_range : DataFrame/Series, Data range --> DataFrame/Series
filters the input data by the date range provided
"""

def data_in_range(data, d_range):
    if (type(d_range)==list):
        return pd.concat(list(map(
                lambda token: range_token_df(data,token),
                d_range))).sort_index()
    else:
        return range_token_df(data,d_range)


"""
time_filter: DataFrame, ... --> DataFrame
filters data by properties like date and time

PARAMETERS:
data : DataFrame or Series with DateTimeIndex
*times: Tuple with start and end time strings as 'HH:MM'
	or list of such tuples
*include: Accepts a DataRange which is:
    1) A datetime index (partial or formal)
    2) A tuple of start and end datetime indexes (See 1)
        	Enter None to set to range min or max
    3) A list that contains any combination of types 1 and 2
*blacklist: range of dates to be excluded.
    See include parameter for acceptable format
    Overrides include parameter
*daysofweek: List of integers for days to be included
	0 = Mon, 6 = Sun
*months: List of integers for months to be included
    1 = Jan, 12 = Dec

starred parameters are optional
ranges are all inclusive
"""

def time_filter(data, **kwds):
    out = data
    if ('include' in kwds):
        out = data_in_range(out,kwds['include'])
    if ('times' in kwds):
        d_range = kwds['times']
        if type(d_range[0]) is tuple:
            out = pd.concat(list(map(
                    lambda subrange: out.between_time(*subrange),
                    d_range))).sort_index()
        else:
            out = out.between_time(*d_range)
    if ('daysofweek' in kwds):
        out = out[[day in kwds['daysofweek'] for day in out.index.weekday]]
    if ('months' in kwds):
        out = out[[month in kwds['months'] for month in out.index.month]]
    if ('blacklist' in kwds):
        out = out.drop(data_in_range(data, kwds['blacklist']).index, errors='ignore')
    return out

"""
convert_range_tz : DataRange(datetime.datetime), timezone --> DataRange
converts the ical default UTC timezone to the desired timezone
"""

def convert_range_tz(range_utc, local_tz):
    convert = lambda time: pytz.utc.localize(
            time.replace(tzinfo=None)).astimezone(
                    local_tz).replace(tzinfo=None)
    return tuple(map(convert,range_utc))

"""
ical_ranges: File Path --> ListOf DataRanges
reads the ics file at the given path, and turns the event start and end times
into data ranges that can be read by the time_filter function
"""
def ical_ranges(file):
    cal = Calendar.from_ical(open(file,'rb').read())
    ranges = []
    cal_tz = pytz.timezone(cal['X-WR-TIMEZONE'])
    for event in cal.subcomponents:
        event_range=(event['dtstart'].dt,event['dtend'].dt)
        if isinstance(event_range[0],datetime.datetime):
            event_range = convert_range_tz(event_range, cal_tz)
        ranges.append(event_range)
    return ranges


"""
mad: Data --> int
Get the median absolute deviation of the Series or each Dataframe column
"""

def mad(data, **kwds):
    return abs(data.sub(data.median(**kwds),axis=0)).median(**kwds)

"""
plot_normal: float, float --> void
Plot a normal distribution with the given mu and sigma
"""
def plot_normal(mu, sigma, **kwds):
    x = np.linspace(mu-8*sigma,mu+8*sigma, 100)
    plt.plot(x,mlab.normpdf(x, mu, sigma), **kwds)

"""
unstack_by_time: Series --> DataFrame
split timestamped series into date columns with common time index
"""
def unstack_by_time(series):
    stacked = series.copy()
    stacked.index = [stacked.index.time, stacked.index.date]
    return stacked.unstack()

"""
consecutives : Data, Offset --> GroupBy
organizes data in sections that are not more than the threshold time span apart
Group labels are just a count starting from 0

Example use:
    consecutives(df_energy, '15 min')
"""
def consecutives(data, threshold):
    dates = pd.Series(data.index, data.index)
    indicators = dates.diff() > pd.Timedelta(threshold)
    groups = indicators.apply(lambda x: 1 if x else 0).cumsum()
    return data.groupby(groups)

"""
energy_trapz : Data [opt: Offset ] --> int
uses a trapezoidal approximation to calculate energy used during the time period
Optional offset parameter determines how large of a time gap between entries
    is the threshold for data grouping
"""

def trapz(data, offset=None):
    if offset is None:
        offset = pd.Timedelta.max
    grouped = consecutives(data,offset)
    approx_kwh = lambda x: np.trapz(x,x.index).astype('timedelta64[h]').astype(int)
    return grouped.aggregate(approx_kwh).sum()

"""
lognorm_params: Series --> ( float, float, float )
Returns the shape, loc, and scale of the lognormal distribution of the sample data
"""

def lognorm_params(series):
    # resolve issues with taking the log of zero
    np.seterr(divide='ignore')
    log_data = np.log(series)
    np.seterr(divide='warn')
    log_data[np.isneginf(log_data)] = 0
    
    kde = stats.gaussian_kde(log_data)
    est_std = mad(log_data)*1.4826
    est_mu = optimize.minimize_scalar(lambda x: -1*kde.pdf(x)[0],
                                      method='bounded',
                                      bounds=(log_data.min(),log_data.max())).x
    return (est_std, 0, math.exp(est_mu))

"""
adjust_sample: Series *int --> Series
returns an adjusted version of the data that approximately follows the
energize fitted lognormal distribution

Buffer count (for setting the quantiles) defaults to 1 on each side (to take
the place of the 0th and 100th percentiles) and can optionally be changed
"""

def adjust_sample(series, buffer=1):
    fit_params = lognorm_params(series)
    s_sorted = series.sort_values()
    q_step = 1/(series.size+2*buffer-1)
    q_array = np.linspace(buffer*q_step, 1-buffer*q_step, series.size)
    quantiles=pd.Series(q_array, s_sorted.index).sort_index()
    return pd.Series(stats.lognorm.ppf(quantiles,*fit_params),
                     quantiles.index)
    
"""
intersect : Data Data --> (Data, Data)
returns a tuple of the data filtered by their common indexes
"""

def intersect(data1, data2):
    ixs = data1.index.intersection(data2.index)
    return(data1.loc[ixs],data2.loc[ixs])
    
data_path = 'resources/2017 Mar - 2016 Aug - Electric - Detail - 24 Hrs.csv'

df_energy = pd.read_csv(data_path, skipfooter=3, engine='python', index_col=0)
df_energy.dropna(inplace=True)
df_energy.index = pd.to_datetime(df_energy.index)

no_school = ical_ranges('resources/no_school_2016-17.ics')
half_days = ical_ranges('resources/half_days_2016-17.ics')

df_school = time_filter(df_energy,
                            times=('07:40','14:20'),
                            include=('9/2/16','6/16/17'),
                            daysofweek=[0,1,2,3,4],
                            blacklist=no_school + half_days
                            + ['2/9/17','2/13/17','3/14/17','3/15/17'])

df_weekend = time_filter(df_energy,daysofweek=[5,6])
df_night = time_filter(df_energy,times=('23:00','04:00'),include=('2016-08',None))
df_summer = time_filter(df_energy,months=[7,8])

df_temp = pd.read_csv('resources/temperature.csv',
                      index_col=1,
                      na_values=-9999).drop('STATION',axis=1)

df_temp.index = pd.to_datetime(df_temp.index,format='%Y%m%d')